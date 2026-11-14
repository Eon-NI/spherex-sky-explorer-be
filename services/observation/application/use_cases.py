from services.observation.application.ports import (
    ObservationCatalog, ObservationRepository, ObservationSearchCachePort, TemporalObservationCatalog,
    TemporalObservationRepository, TemporalSearchCachePort,
)
from services.observation.domain.models import Observation, SkySearch, TemporalObservation, TemporalSearch


class SearchObservations:
    def __init__(self, catalog: ObservationCatalog,
                 repository: ObservationRepository | None = None,
                 cache: ObservationSearchCachePort | None = None):
        self.catalog, self.repository, self.cache = catalog, repository, cache

    def execute(self, query: SkySearch) -> list[Observation]:
        if self.cache is not None:
            cached = self.cache.get(query)
            if cached is not None:
                return cached
        results = self.catalog.search(query)
        if self.repository is not None:
            changed = self.repository.upsert(results)
            if changed and self.cache is not None:
                self.cache.invalidate_all()
        if self.cache is not None:
            self.cache.put(query, results)
        return results


class SearchTemporalObservations:
    def __init__(self, catalog: TemporalObservationCatalog, repository: TemporalObservationRepository,
                 cache: TemporalSearchCachePort | None = None):
        self.catalog, self.repository, self.cache = catalog, repository, cache

    def execute(self, query: TemporalSearch) -> list[TemporalObservation]:
        if self.cache is not None:
            cached = self.cache.get(query)
            if cached is not None:
                return cached
        results = self.catalog.search(query)
        self.repository.upsert(results)
        if self.cache is not None:
            self.cache.put(query, results)
        return results


class SyncObservations:
    def __init__(self, catalog: ObservationCatalog, repository: ObservationRepository,
                 cache: ObservationSearchCachePort | None = None):
        self.catalog, self.repository, self.cache = catalog, repository, cache

    def execute(self, query: SkySearch) -> int:
        changed = self.repository.upsert(self.catalog.search(query))
        if changed and self.cache is not None:
            self.cache.invalidate_all()
        return changed
