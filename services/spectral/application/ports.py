from typing import Protocol
from services.spectral.domain.models import SpectralChannel, SpectralSample, SpectrumRequest


class SpectralCube(Protocol):
    def channels(self) -> list[SpectralChannel]: ...
    def extract(self, request: SpectrumRequest) -> list[SpectralSample]: ...
