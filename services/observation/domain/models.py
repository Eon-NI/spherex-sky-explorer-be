from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class SkySearch:
    ra_deg: float
    dec_deg: float
    radius_deg: float

    def __post_init__(self) -> None:
        if not all(map(isfinite, (self.ra_deg, self.dec_deg, self.radius_deg))):
            raise ValueError("Coordinates and radius must be finite")
        if not 0 <= self.ra_deg < 360 or not -90 <= self.dec_deg <= 90:
            raise ValueError("Coordinates outside valid ICRS range")
        if not 0 < self.radius_deg <= 1:
            raise ValueError("radius_deg must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class Observation:
    publisher_did: str
    collection: str
    access_url: str
    ra_deg: float | None
    dec_deg: float | None
