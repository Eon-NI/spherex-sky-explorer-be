from services.observation.application.use_cases import SearchObservations, SyncObservations
from services.observation.domain.models import Observation, SkySearch


OBS = Observation("did", "spherex_qr3", "https://irsa.ipac.caltech.edu/a.fits", 1, 2)
QUERY = SkySearch(1, 2, 0.01)


class Catalog:
    calls = 0
    def search(self, query):
        self.calls += 1
        return [OBS]


class Repository:
    def __init__(self, changed=1):
        self.changed = changed
        self.saved = []
    def upsert(self, observations):
        self.saved = observations
        return self.changed


class Cache:
    def __init__(self, value=None):
        self.value = value
        self.invalidations = 0
        self.puts = []
    def get(self, query):
        return self.value
    def put(self, query, observations):
        self.puts.append((query, observations))
    def invalidate_all(self):
        self.invalidations += 1


def test_cache_hit_avoids_irsa_and_database_writes():
    catalog, repository, cache = Catalog(), Repository(), Cache([OBS])
    assert SearchObservations(catalog, repository, cache).execute(QUERY) == [OBS]
    assert catalog.calls == 0 and repository.saved == []


def test_cache_miss_persists_metadata_and_invalidates_old_snapshots():
    catalog, repository, cache = Catalog(), Repository(), Cache()
    assert SearchObservations(catalog, repository, cache).execute(QUERY) == [OBS]
    assert catalog.calls == 1 and repository.saved == [OBS]
    assert cache.invalidations == 1 and len(cache.puts) == 1


def test_sync_invalidates_only_for_changed_metadata():
    catalog, repository, cache = Catalog(), Repository(changed=0), Cache()
    assert SyncObservations(catalog, repository, cache).execute(QUERY) == 0
    assert cache.invalidations == 0
    repository.changed = 1
    assert SyncObservations(catalog, repository, cache).execute(QUERY) == 1
    assert cache.invalidations == 1
