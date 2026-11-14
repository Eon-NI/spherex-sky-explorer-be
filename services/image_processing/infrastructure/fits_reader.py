"""Read compact scientific metadata from a SPHEREx spectral-image MEF."""
from math import isfinite
from pathlib import Path
import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from services.image_processing.domain.models import FitsMetadata


def _header_text(header, key: str) -> str | None:
    value = header.get(key)
    return None if value is None else str(value)


def _header_float(header, key: str) -> float | None:
    value = header.get(key)
    if value is None:
        return None
    value = float(value)
    return value if isfinite(value) else None


class AstropyFitsReader:
    def inspect(self, path: Path, ra_deg: float | None = None,
                dec_deg: float | None = None) -> FitsMetadata:
        if (ra_deg is None) != (dec_deg is None):
            raise ValueError("RA and Dec must be supplied together")
        with fits.open(path, memmap=True, lazy_load_hdus=True) as hdul:
            if "IMAGE" not in hdul:
                raise ValueError("FITS IMAGE extension is missing")
            image = hdul["IMAGE"]
            if image.data is None or image.data.ndim != 2:
                raise ValueError("FITS IMAGE must be two-dimensional")
            height, width = map(int, image.data.shape)
            if height < 1 or width < 1:
                raise ValueError("FITS IMAGE is empty")
            data = image.data
            finite = np.isfinite(data)
            count = int(np.count_nonzero(finite))
            stats: dict[str, float | int | None] = {
                "finite_pixels": count,
                "min": float(np.min(data[finite])) if count else None,
                "max": float(np.max(data[finite])) if count else None,
                "mean": float(np.mean(data[finite], dtype=np.float64)) if count else None,
            }
            flags = hdul["FLAGS"] if "FLAGS" in hdul else None
            flags_shape = None
            flags_names: dict[str, str] = {}
            if flags is not None:
                if flags.data is None or flags.data.ndim != 2:
                    raise ValueError("FITS FLAGS must be two-dimensional")
                flags_shape = tuple(int(x) for x in flags.data.shape)
                if flags_shape != (height, width):
                    raise ValueError("FITS FLAGS dimensions differ from IMAGE")
                flags_names = {key: str(value) for key, value in flags.header.items()
                               if key.startswith("MSKN")}
            spatial = WCS(image.header).celestial
            if spatial.pixel_n_dim != 2 or spatial.world_n_dim != 2:
                raise ValueError("FITS spatial WCS is invalid")
            pixel_position = None
            sky_position = None
            wavelength_um = None
            bandwidth_um = None
            if ra_deg is not None and dec_deg is not None:
                sky = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
                x, y = spatial.world_to_pixel(sky)
                if not (isfinite(x) and isfinite(y) and 0 <= x < width and 0 <= y < height):
                    raise ValueError("Requested coordinate is outside FITS cutout")
                pixel_position = (float(x), float(y))
                back = spatial.pixel_to_world(x, y)
                sky_position = (float(back.ra.deg), float(back.dec.deg))
                if "WCS-WAVE" in hdul:
                    spectral = WCS(header=image.header, fobj=hdul, key="W")
                    spectral.sip = None
                    wavelength, bandwidth = spectral.pixel_to_world(x, y)
                    wavelength_um = float(wavelength.to_value(u.um))
                    bandwidth_um = float(bandwidth.to_value(u.um))
                    if not (isfinite(wavelength_um) and isfinite(bandwidth_um)):
                        raise ValueError("Spectral WCS returned non-finite values")
            return FitsMetadata(
                version=_header_text(hdul[0].header, "VERSION"),
                image_shape=(height, width), image_unit=_header_text(image.header, "BUNIT"),
                detector=_header_text(image.header, "DETECTOR"),
                obs_id=_header_text(image.header, "OBSID"),
                header_date=_header_text(image.header, "DATE"),
                psf_fwhm=_header_float(image.header, "PSF_FWHM"),
                image_statistics=stats,
                flags_available=flags is not None, flags_shape=flags_shape,
                flags_header_names=flags_names,
                sky_position_deg=sky_position, pixel_position_xy=pixel_position,
                wavelength_um=wavelength_um, bandwidth_um=bandwidth_um,
            )
