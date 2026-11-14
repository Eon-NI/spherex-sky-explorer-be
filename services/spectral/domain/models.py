from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpectrumRequest:
    ra_deg: float
    dec_deg: float
    aperture_arcsec: float


@dataclass(frozen=True, slots=True)
class SpectralSample:
    wavelength_um: float
    flux_mjy_sr: float
    uncertainty_mjy_sr: float | None
