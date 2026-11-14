"""Framework-free catalog requests and scientific provenance."""
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
import re

COLLECTIONS = ("spherex_qr3", "spherex_qr3_deep", "spherex_dr1", "spherex_dr1_deep")
DETECTORS = tuple(f"SPHEREx-D{number}" for number in range(1, 7))


def validate_position(ra_deg: float, dec_deg: float) -> None:
    if not isfinite(ra_deg) or not isfinite(dec_deg):
        raise ValueError("Coordinates must be finite")
    if not 0 <= ra_deg < 360 or not -90 <= dec_deg <= 90:
        raise ValueError("Coordinates outside ICRS range")


@dataclass(frozen=True, slots=True)
class SkySearch:
    ra_deg: float
    dec_deg: float
    radius_deg: float
    collections: tuple[str, ...] = ("spherex_qr3", "spherex_dr1")

    def __post_init__(self) -> None:
        validate_position(self.ra_deg, self.dec_deg)
        if not isfinite(self.radius_deg) or not 0 < self.radius_deg <= 1:
            raise ValueError("radius_deg must be in (0, 1]")
        if not self.collections or any(item not in COLLECTIONS for item in self.collections):
            raise ValueError("Unsupported SPHEREx collection")


@dataclass(frozen=True, slots=True)
class Observation:
    publisher_did: str
    collection: str
    access_url: str
    ra_deg: float | None
    dec_deg: float | None
    obs_id: str | None = None
    access_format: str | None = None
    s_region: str | None = None
    energy_bandpassname: str | None = None
    t_min_mjd: float | None = None
    t_max_mjd: float | None = None
    em_min_m: float | None = None
    em_max_m: float | None = None
    missing_fields: tuple[str, ...] = ()
    processing_version: str | None = None


@dataclass(frozen=True, slots=True)
class TemporalSearch:
    ra_deg: float
    dec_deg: float
    size_deg: float
    detector: str
    limit: int = 100

    def __post_init__(self) -> None:
        validate_position(self.ra_deg, self.dec_deg)
        if not isfinite(self.size_deg) or not 0 < self.size_deg <= 1:
            raise ValueError("size_deg must be in (0, 1]")
        if self.detector not in DETECTORS:
            raise ValueError("Unsupported detector")
        if not isinstance(self.limit, int) or not 1 <= self.limit <= 500:
            raise ValueError("limit must be in [1, 500]")


@dataclass(frozen=True, slots=True)
class TemporalObservation:
    product_id: str
    artifact_uri: str
    cutout_url: str
    time_bounds_lower: float
    ra_deg: float
    dec_deg: float
    size_deg: float
    detector: str

    @classmethod
    def product_key(cls, artifact_uri: str, ra_deg: float, dec_deg: float,
                    size_deg: float, detector: str, time_bounds_lower: float) -> str:
        payload = (f"{artifact_uri}:{ra_deg:.12g}:{dec_deg:.12g}:"
                   f"{size_deg:.12g}:{detector}:{time_bounds_lower:.12g}")
        return sha256(payload.encode("utf-8")).hexdigest()
