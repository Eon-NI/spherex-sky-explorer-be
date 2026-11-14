from services.observation.application.ports import ObservationCatalog, ObservationRepository
from services.observation.domain.models import Observation, SkySearch


class SearchObservations:
    def __init__(self, catalog: ObservationCatalog):
        self.catalog = catalog

    def execute(self, query: SkySearch) -> list[Observation]:
        return self.catalog.search(query)


class SyncObservations:
    def __init__(self, catalog: ObservationCatalog, repository: ObservationRepository):
        self.catalog, self.repository = catalog, repository

    def execute(self, query: SkySearch) -> int:
        return self.repository.upsert(self.catalog.search(query))
