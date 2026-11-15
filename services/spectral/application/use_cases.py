"""Explore one official DR1 HiPS tile; retain units and missing samples."""
import math
import struct
import zlib

import numpy as np
from astropy import units as u
from astropy.wcs import WCS

from services.spectral.domain.models import BLANK_DICHROIC_CHANNELS, DR1_HIPS_PRODUCT_ID


def inspect_cube(hdul):
    for name in ("IMAGE", "NHITS", "FLAGS", "SPECTRAL_CHANNELS", "WCS-WAVE"):
        if name not in hdul:
            raise ValueError(f"Missing FITS extension {name}")
    shape = hdul["IMAGE"].data.shape
    if len(shape) != 3 or shape[0] != 102 or shape[1:] != hdul["NHITS"].data.shape[1:]:
        raise ValueError("Invalid IMAGE/NHITS cube shape")
    if hdul["FLAGS"].data.shape != shape or len(hdul["SPECTRAL_CHANNELS"].data) != 102:
        raise ValueError("Invalid FLAGS/channel table shape")
    wcs = WCS(hdul["IMAGE"].header, fobj=hdul)
    if wcs.celestial.naxis != 2:
        raise ValueError("Invalid celestial WCS")
    return wcs


def wavelength_at(wcs, x: float, y: float, index: int) -> float | None:
    try:
        world = wcs.all_pix2world([[x, y, index]], 0)[0]
        spectral_axis = wcs.wcs.spec
        value = (world[spectral_axis] * u.Unit(wcs.wcs.cunit[spectral_axis])).to_value(u.um)
        return float(value) if math.isfinite(value) and value > 0 else None
    except (ValueError, TypeError, IndexError, u.UnitConversionError):
        return None


def pixel_at(wcs, ra_deg: float, dec_deg: float, shape):
    x, y = wcs.celestial.all_world2pix([[ra_deg, dec_deg]], 0)[0]
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("Sky coordinate has no tile pixel")
    ix, iy = round(x), round(y)
    if not (0 <= ix < shape[2] and 0 <= iy < shape[1]):
        raise ValueError("Sky coordinate outside tile")
    return float(x), float(y), ix, iy


def channels_from_fits(hdul):
    table = hdul["SPECTRAL_CHANNELS"]
    names = set(table.columns.names)
    if not {"DETECTOR", "SUBCHAN", "WAVELENGTH"} <= names:
        raise ValueError("Incomplete SPECTRAL_CHANNELS table")
    unit = next(column.unit for column in table.columns if column.name == "WAVELENGTH")
    if unit is None:
        raise ValueError("WAVELENGTH column has no unit")
    values = []
    for index, row in enumerate(table.data):
        wavelength = (float(row["WAVELENGTH"]) * u.Unit(unit)).to_value(u.um)
        values.append({"channel_index": index, "band": int(row["DETECTOR"]),
                       "band_channel": int(row["SUBCHAN"]),
                       "wavelength_um": float(wavelength),
                       "available": index not in BLANK_DICHROIC_CHANNELS})
    if len(values) != 102:
        raise ValueError("Expected 102 spectral channels")
    return values


def cube_metadata(hdul, tile: int, ra_deg: float, dec_deg: float, fov_deg: float, channels):
    wcs = inspect_cube(hdul)
    x, y, _, _ = pixel_at(wcs, ra_deg, dec_deg, hdul["IMAGE"].data.shape)
    table = hdul["SPECTRAL_CHANNELS"]
    column_meta = [{"name": column.name, "unit": column.unit} for column in table.columns]
    table_rows = []
    for row in table.data:
        values = {}
        for column in table.columns:
            value = row[column.name]
            if np.issubdtype(np.asarray(value).dtype, np.number):
                numeric = float(value)
                values[column.name] = (int(value) if np.issubdtype(np.asarray(value).dtype, np.integer)
                                       else numeric if math.isfinite(numeric) else None)
            else:
                values[column.name] = str(value).strip()
        table_rows.append(values)
    return {"product_id": DR1_HIPS_PRODUCT_ID, "tile_id": tile,
            "ra_deg": ra_deg, "dec_deg": dec_deg, "fov_deg": fov_deg,
            "image_unit": hdul["IMAGE"].header.get("BUNIT"),
            "flags_header_names": {key: str(value) for key, value in hdul["FLAGS"].header.items()
                                   if key.startswith("MSKN")},
            "image_shape": list(hdul["IMAGE"].data.shape),
            "pixel_position_xy": [x, y], "extensions": ["IMAGE", "NHITS", "FLAGS",
                                                          "SPECTRAL_CHANNELS", "WCS-WAVE"],
            "spectral_channel_table": {"columns": column_meta, "rows": table_rows},
            "channels": channels}


def extract_spectrum(hdul, tile: int, ra_deg: float, dec_deg: float, channels):
    wcs = inspect_cube(hdul)
    _, _, ix, iy = pixel_at(wcs, ra_deg, dec_deg, hdul["IMAGE"].data.shape)
    image = hdul["IMAGE"].data
    hits = hdul["NHITS"].data
    flags = hdul["FLAGS"].data
    points = []
    for channel in channels:
        index = channel["channel_index"]
        raw_flux = float(image[index, iy, ix])
        nhits = int(hits[index, iy, ix])
        available = index not in BLANK_DICHROIC_CHANNELS and nhits > 0 and math.isfinite(raw_flux)
        points.append({"channel_index": index,
                       "wavelength_um": wavelength_at(wcs, ix, iy, index),
                       "flux": raw_flux if available else None,
                       "nhits": nhits, "flags": int(flags[index, iy, ix]),
                       "available": available})
    return {"product_id": DR1_HIPS_PRODUCT_ID, "tile_id": tile, "ra_deg": ra_deg,
            "dec_deg": dec_deg, "unit": hdul["IMAGE"].header.get("BUNIT"),
            "wavelength_unit": "um", "method": "single_pixel_quicklook",
            "flags_header_names": {key: str(value) for key, value in hdul["FLAGS"].header.items()
                                   if key.startswith("MSKN")},
            "points": points}


def _png_gray(array: np.ndarray) -> bytes:
    height, width = array.shape
    finite = array[np.isfinite(array)]
    if finite.size:
        low, high = np.percentile(finite, [2, 98])
        if high <= low:
            high = low + 1
        pixels = (np.clip((np.nan_to_num(array, nan=low) - low) / (high - low), 0, 1) * 255).astype("uint8")
    else:
        pixels = np.zeros((height, width), dtype="uint8")
    rows = b"".join(b"\x00" + row.tobytes() for row in pixels[::-1])
    def part(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + part(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + part(b"IDAT", zlib.compress(rows, 6)) + part(b"IEND", b""))


def preview_bounds(hdul, ra_deg: float, dec_deg: float, fov_deg: float):
    wcs = inspect_cube(hdul)
    x, y, _, _ = pixel_at(wcs, ra_deg, dec_deg, hdul["IMAGE"].data.shape)
    # Derive the requested square's pixel bounds from its four sky corners.
    half = fov_deg / 2
    sky = [[(ra_deg + dx / max(math.cos(math.radians(dec_deg)), 0.01)) % 360,
            max(-90, min(90, dec_deg + dy))] for dx in (-half, half) for dy in (-half, half)]
    corners = wcs.celestial.all_world2pix(sky, 0)
    radius = int(max(8, min(256, np.max(np.abs(corners - [x, y])))))
    plane = hdul["IMAGE"].data[0]
    ny, nx = plane.shape
    x0, x1 = max(0, int(x) - radius), min(nx, int(x) + radius)
    y0, y1 = max(0, int(y) - radius), min(ny, int(y) + radius)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Empty preview")
    return x0, x1, y0, y1, wcs


def preview_png(hdul, channel_index: int, ra_deg: float, dec_deg: float, fov_deg: float) -> bytes:
    if channel_index in BLANK_DICHROIC_CHANNELS:
        raise ValueError("Blank dichroic channel")
    x0, x1, y0, y1, _ = preview_bounds(hdul, ra_deg, dec_deg, fov_deg)
    plane = hdul["IMAGE"].data[channel_index]
    return _png_gray(plane[y0:y1, x0:x1])


def preview_point(hdul, ra_deg: float, dec_deg: float, fov_deg: float, u: float, v: float):
    x0, x1, y0, y1, wcs = preview_bounds(hdul, ra_deg, dec_deg, fov_deg)
    x = x0 + u * (x1 - x0 - 1)
    y = y1 - 1 - v * (y1 - y0 - 1)
    sky = wcs.celestial.all_pix2world([[x, y]], 0)[0]
    if not all(math.isfinite(value) for value in sky):
        raise ValueError("Invalid preview point")
    return {"ra_deg": float(sky[0] % 360), "dec_deg": float(sky[1])}
