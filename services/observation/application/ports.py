from typing import Protocol
from services.observation.domain.models import Observation, SkySearch


class ObservationCatalog(Protocol):
    def search(self, query: SkySearch) -> list[Observation]: ...


class ObservationRepository(Protocol):
    def upsert(self, observations: list[Observation]) -> int: ...
