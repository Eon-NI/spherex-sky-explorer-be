from dataclasses import dataclass
from math import isfinite


DR1_HIPS_PRODUCT_ID = "ivo://IPAC/P/SPHEREx/DR1/cube"
BLANK_DICHROIC_CHANNELS = frozenset({50, 51})


@dataclass(frozen=True, slots=True)
class SpectrumRequest:
    ra_deg: float
    dec_deg: float
    fov_deg: float

    def __post_init__(self):
        if not all(isfinite(value) for value in (self.ra_deg, self.dec_deg, self.fov_deg)):
            raise ValueError("Spectral coordinates and FOV must be finite")
        if not (0 <= self.ra_deg < 360 and -90 <= self.dec_deg <= 90 and
                0.01 <= self.fov_deg <= 1):
            raise ValueError("Spectral coordinates or FOV outside supported range")


@dataclass(frozen=True, slots=True)
class SpectralChannel:
    channel_index: int
    band: int
    band_channel: int
    wavelength_um: float
    available: bool


@dataclass(frozen=True, slots=True)
class SpectralSample:
    channel_index: int
    wavelength_um: float | None
    flux: float | None
    nhits: int
    flags: int
    available: bool
