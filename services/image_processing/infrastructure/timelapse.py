"""Small deterministic FITS -> north-up TAN -> common asinh -> browser PNG pipeline."""
import re
from hashlib import sha256
from math import ceil
from pathlib import Path

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
from PIL import Image, ImageDraw

from services.image_processing.domain.timelapse import TimelapseRequest

SCALE_ARCSEC = 6.2
MAX_INPUT_SIDE = 512
MAX_OUTPUT_SIDE = 256


def target_grid(request):
    n = max(1, ceil(request.size_deg * 3600 / SCALE_ARCSEC))
    if n > MAX_OUTPUT_SIDE:
        raise ValueError("Output grid exceeds MVP bounds")
    grid = WCS(naxis=2)
    grid.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    grid.wcs.cunit = ["deg", "deg"]
    grid.wcs.crval = [request.ra_deg, request.dec_deg]
    grid.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    grid.wcs.cdelt = [-SCALE_ARCSEC / 3600, SCALE_ARCSEC / 3600]
    grid.wcs.radesys = "ICRS"
    return grid, (n, n)


def acquisition_time(header):
    scale = str(header.get("TIMESYS", "UTC")).strip().lower()
    if scale not in Time.SCALES:
        raise ValueError("Invalid FITS TIMESYS")
    if "DATE-OBS" in header:
        raw, fmt = str(header["DATE-OBS"]).strip().removesuffix("Z"), "isot"
    elif "MJD-OBS" in header:
        raw, fmt = float(header["MJD-OBS"]), "mjd"
    else:
        raise ValueError("FITS acquisition DATE-OBS/MJD-OBS missing; DATE is not observation time")
    if fmt == "isot" and "T" not in raw:
        raise ValueError("FITS acquisition time must include both date and time")
    t = Time(raw, format=fmt, scale=scale).utc
    if not np.isfinite(t.mjd):
        raise ValueError("Invalid FITS acquisition time")
    if "MJD-OBS" in header:
        mjd = Time(float(header["MJD-OBS"]), format="mjd", scale=scale).utc
        if not np.isfinite(mjd.mjd) or abs((mjd - t).to_value(u.s)) > 1:
            raise ValueError("DATE-OBS and MJD-OBS disagree")
    return t, {"time_keyword": "DATE-OBS" if fmt == "isot" else "MJD-OBS",
               "time_original": raw, "time_scale": scale, "timesys_default": "TIMESYS" not in header}


def align_bilinear(data, source, grid, shape):
    """Inverse celestial mapping; conservative NaN interpolation, no SciPy dependency."""
    yy, xx = np.indices(shape)
    sky = grid.pixel_to_world(xx, yy)
    x, y = source.world_to_pixel(sky)
    inside = np.isfinite(x) & np.isfinite(y) & (x >= 0) & (y >= 0)
    inside &= (x <= data.shape[1] - 1) & (y <= data.shape[0] - 1)
    x = np.clip(np.where(inside, x, 0), 0, data.shape[1] - 1)
    y = np.clip(np.where(inside, y, 0), 0, data.shape[0] - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    dx, dy = x - x0, y - y0
    values, weights = np.zeros(shape), np.zeros(shape)
    for ox, oy, weight in ((0, 0, (1-dx)*(1-dy)), (1, 0, dx*(1-dy)),
                           (0, 1, (1-dx)*dy), (1, 1, dx*dy)):
        sample = data[np.minimum(y0+oy, data.shape[0]-1), np.minimum(x0+ox, data.shape[1]-1)]
        valid = np.isfinite(sample)
        values += np.where(valid, sample, 0) * weight
        weights += valid * weight
    return np.where(inside & (weights >= 0.999999), values, np.nan)


def read_frame(product, path, request, grid, shape):
    with fits.open(path, memmap=False, lazy_load_hdus=True) as hdul:
        hdul.verify("exception")
        if "IMAGE" not in hdul:
            raise ValueError("FITS IMAGE extension missing")
        image = hdul["IMAGE"]
        # Check header dimensions BEFORE allocating/scaling the science array.
        if image.header.get("NAXIS") != 2:
            raise ValueError("IMAGE must be explicitly 2D; cubes are not temporal planes")
        axes = [image.header.get(f"NAXIS{i}", 0) for i in (1, 2)]
        if any(not 1 <= value <= MAX_INPUT_SIDE for value in axes):
            raise ValueError("IMAGE dimensions exceed bounded cutout limits")
        header = hdul[0].header.copy()
        header.update(image.header)
        if str(header.get("DETECTOR", "")) != request.detector.removeprefix("SPHEREx-D"):
            raise ValueError("FITS detector differs from Observation reference")
        obsid = str(header.get("OBSID", "")).strip()
        named = re.search(r"/level2_([^/]+)D([1-6])_spx_", product.artifact_uri)
        if named and (named[1] != obsid or named[2] != request.detector.removeprefix("SPHEREx-D")):
            raise ValueError("FITS OBSID/detector differs from artifact filename")
        if not obsid:
            raise ValueError("FITS OBSID required for duplicate/provenance verification")
        unit = u.Unit(image.header.get("BUNIT", ""))
        if not unit.is_equivalent(u.MJy / u.sr):
            raise ValueError("IMAGE BUNIT must be surface brightness convertible to MJy/sr")
        data = np.array(image.data, dtype=float) * unit.to(u.MJy / u.sr)
        if data.shape != (axes[1], axes[0]):
            raise ValueError("Invalid IMAGE shape")
        for name in ("FLAGS", "VARIANCE"):
            if name in hdul and hdul[name].shape != data.shape:
                raise ValueError(f"{name} dimensions differ from IMAGE")
        data[~np.isfinite(data)] = np.nan
        if "VARIANCE" in hdul:
            variance = hdul["VARIANCE"].data
            data[~np.isfinite(variance) | (variance < 0)] = np.nan
        spatial = WCS(image.header, fobj=hdul).celestial
        if not spatial.has_celestial:
            raise ValueError("Missing celestial WCS")
        coord = SkyCoord(request.ra_deg * u.deg, request.dec_deg * u.deg, frame="icrs")
        x, y = spatial.world_to_pixel(coord)
        if not (np.isfinite(x) and np.isfinite(y) and 0 <= x < axes[0] and 0 <= y < axes[1]):
            raise ValueError("Requested coordinate outside FITS coverage")
        t, metadata = acquisition_time(header)
        aligned = align_bilinear(data, spatial, grid, shape)
        coverage = float(np.isfinite(aligned).mean())
        if not coverage:
            raise ValueError("No valid coverage after reprojection")
        wavelength, bandwidth = None, None
        if "WCS-WAVE" in hdul:
            spectral = WCS(image.header, fobj=hdul, key="W")
            spectral.sip = None  # Preserve SIP in the spatial WCS.
            wl, bw = spectral.pixel_to_world(x, y)
            wavelength, bandwidth = float(wl.to_value(u.um)), float(bw.to_value(u.um))
            if not all(np.isfinite(v) and v > 0 for v in (wavelength, bandwidth)):
                raise ValueError("Invalid position-dependent spectral WCS")
        flags = hdul["FLAGS"].header if "FLAGS" in hdul else {}
        metadata.update({"obs_id": obsid, "detector": request.detector,
                         "timestamp": t.isot + "Z", "mjd_utc": float(t.mjd),
                         "tap_time_bounds_lower": product.time_bounds_lower,
                         "tap_time_interpretation": "Unconverted original BE-01 value: no unit/scale contract",
                         "coverage_fraction": coverage, "unit": "MJy/sr",
                         "wavelength_um": wavelength, "bandwidth_um": bandwidth,
                         "fits_version": str(hdul[0].header.get("VERSION", "unknown")),
                         "flags_header_names": {k: str(v) for k, v in flags.items() if k.startswith("MSKN")},
                         "source_url": product.cutout_url, "artifact_uri": product.artifact_uri,
                         "source_sha256": sha256(path.read_bytes()).hexdigest(),
                         "source_object_key": f"{request.prefix}/source-{product.product_id}.fits"})
    return {"product_id": product.product_id, "image": aligned, "metadata": metadata}


def common_limits(frames):
    valid = np.concatenate([f["image"][np.isfinite(f["image"])] for f in frames])
    if not valid.size:
        raise ValueError("No finite pixels")
    low, high = map(float, np.percentile(valid, [1, 99.5]))
    if high <= low:
        high = low + max(abs(low)*1e-6, 1e-6)
    return low, high


def intensity_pixels(image, low, high):
    finite = np.isfinite(image)
    scaled = np.arcsinh(np.clip((np.where(finite, image, low)-low)/(high-low), 0, 1)/0.1)/np.arcsinh(10)
    gray = np.rint(255*scaled).astype(np.uint8)
    rgb = np.repeat(gray[..., None], 3, axis=2)
    rgb[~finite] = [55, 70, 85]
    return rgb


class FitsSequenceRenderer:
    def render(self, inputs, request: TimelapseRequest, directory: Path):
        grid, shape = target_grid(request)
        frames = [read_frame(product, path, request, grid, shape) for product, path in inputs]
        # Same acquisition/detector/OBSID => choose highest documented path version.
        def version(frame):
            match = re.search(r"l2b-v([0-9.]+)-", frame["metadata"]["artifact_uri"])
            return tuple(int(n) for n in match[1].split(".")) if match else ()
        frames.sort(key=lambda f: (version(f), f["product_id"]), reverse=True)
        unique, discarded = {}, []
        for frame in frames:
            meta = frame["metadata"]
            identity = (meta["obs_id"], meta["detector"], meta["timestamp"])
            if identity in unique:
                discarded.append({"product_id": frame["product_id"], "kept": unique[identity]["product_id"]})
            else:
                unique[identity] = frame
        frames = sorted(unique.values(), key=lambda f: (f["metadata"]["mjd_utc"], f["product_id"]))
        low, high = common_limits(frames)
        result = []
        for index, frame in enumerate(frames):
            # FITS origin lower -> browser origin upper. Upsampling is display-only.
            panel = Image.fromarray(intensity_pixels(frame["image"], low, high)[::-1]).resize((512, 512), Image.Resampling.NEAREST)
            canvas = Image.new("RGB", (512, 560), (15, 15, 15))
            canvas.paste(panel)
            meta = frame["metadata"]
            draw = ImageDraw.Draw(canvas)
            draw.text((5, 516), f"{meta['timestamp']}  frame {index+1}/{len(frames)}", fill="white")
            draw.text((5, 535), f"{meta['detector']}  wavelength={meta['wavelength_um']} um  coverage={meta['coverage_fraction']:.1%}", fill="white")
            canvas.save(directory / f"frame-{index:03d}.png", format="PNG")
            result.append({"index": index, "product_id": frame["product_id"],
                           "timestamp": meta["timestamp"], "object_key": f"{request.prefix}/frame-{index:03d}.png"})
        provenance = {"pipeline": "be02-v1", "manifest_object_key": f"{request.prefix}/manifest.json",
                      "grid": {"header": dict(grid.to_header()), "shape": list(shape), "scale_arcsec": SCALE_ARCSEC},
                      "normalization": {"stretch": "asinh", "a": 0.1, "vmin": low, "vmax": high, "unit": "MJy/sr"},
                      "observations": [f["metadata"] for f in frames], "discarded_duplicates": discarded,
                      "distinct_acquisition_times": len({f["metadata"]["timestamp"] for f in frames}),
                      "warning": "Exploratory: wavelength varies by exposure/position; brightness changes are not proof of variability. FLAGS labels retained, bitmap not interpreted; no quality-mask calibration."}
        return result, provenance
