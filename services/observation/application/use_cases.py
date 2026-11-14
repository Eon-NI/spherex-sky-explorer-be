from services.observation.application.ports import (
    ObservationCatalog,
    ObservationRepository,
    ObservationSearchCachePort,
    TemporalObservationCatalog,
    TemporalObservationRepository,
    TemporalSearchCachePort,
)
from services.observation.domain.models import (
    Observation,
    SkySearch,
    TemporalObservation,
    TemporalSearch,
)


class SearchObservations:
    def __init__(self, catalog: ObservationCatalog,
                 repository: ObservationRepository | None = None,
                 cache: ObservationSearchCachePort | None = None):
        self.catalog, self.repository, self.cache = catalog, repository, cache

    def execute(self, query: SkySearch) -> list[Observation]:
        if self.cache is not None:
            cached = self.cache.get(query)
            if cached is not None:
                return unique_products(cached)
        results = unique_products(self.catalog.search(query))
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
        changed = self.repository.upsert(unique_products(self.catalog.search(query)))
        if changed and self.cache is not None:
            self.cache.invalidate_all()
        return changed


def unique_products(observations: list[Observation]) -> list[Observation]:
    """Same DID = same published product; keep the first in provider priority order.

    Empty DID is a provider error: it cannot be persisted or reliably rehydrated.
    Versions with distinct DIDs remain distinct; no scientific exposure redesign.
    """
    unique = {}
    for item in observations:
        if not item.publisher_did or not item.access_url:
            raise ValueError("IRSA product identity or access URL missing")
        unique.setdefault(item.publisher_did, item)
    return list(unique.values())


class GetTemporalProduct:
    def __init__(self, repository: TemporalObservationRepository):
        self.repository = repository

    def execute(self, product_id: str) -> TemporalObservation | None:
        return self.repository.get(product_id)
