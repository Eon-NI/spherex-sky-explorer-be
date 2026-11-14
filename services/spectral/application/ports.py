from typing import Protocol
from services.spectral.domain.models import SpectralSample, SpectrumRequest


class SpectralCube(Protocol):
    def channels(self) -> list[float]: ...
    def extract(self, request: SpectrumRequest) -> list[SpectralSample]: ...
