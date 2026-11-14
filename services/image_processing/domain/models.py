"""Cutout identity and scientific metadata without framework dependencies."""
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite


@dataclass(frozen=True, slots=True)
class ImageReference:
    object_key: str
    content_type: str = "application/fits"


@dataclass(frozen=True, slots=True)
class CutoutRequest:
    product_id: str
    ra_deg: float
    dec_deg: float
    size_deg: float

    def __post_init__(self) -> None:
        if not isfinite(self.ra_deg) or not isfinite(self.dec_deg):
            raise ValueError("Coordinates must be finite")
        if not 0 <= self.ra_deg < 360 or not -90 <= self.dec_deg <= 90:
            raise ValueError("Coordinates outside ICRS range")
        if not isfinite(self.size_deg) or not 0 < self.size_deg <= 1:
            raise ValueError("size_deg must be in (0, 1]")
        if len(self.product_id) != 64 or any(c not in "0123456789abcdef" for c in self.product_id):
            raise ValueError("Invalid product_id")

    @property
    def idempotency_key(self) -> str:
        payload = f"{self.product_id}:{self.ra_deg:.12g}:{self.dec_deg:.12g}:{self.size_deg:.12g}"
        return sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceProduct:
    product_id: str
    artifact_uri: str
    cutout_url: str
    ra_deg: float
    dec_deg: float
    size_deg: float
    detector: str
    time_bounds_lower: float


@dataclass(frozen=True, slots=True)
class FitsMetadata:
    version: str | None
    image_shape: tuple[int, int]
    image_unit: str | None
    detector: str | None
    obs_id: str | None
    header_date: str | None
    psf_fwhm: float | None
    image_statistics: dict[str, float | int | None]
    flags_available: bool
    flags_shape: tuple[int, int] | None
    flags_header_names: dict[str, str]
    sky_position_deg: tuple[float, float] | None
    pixel_position_xy: tuple[float, float] | None
    wavelength_um: float | None
    bandwidth_um: float | None
