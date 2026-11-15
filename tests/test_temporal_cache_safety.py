"""Conservative cache boundaries: legacy snapshots do not certify completeness.

Mocked Redis/ORM ports exercise the real TemporalSearchCache; no database writes.
"""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from django.utils import timezone

from services.observation.application.use_cases import SearchTemporalObservations
from services.observation.domain.models import TemporalObservation, TemporalSearch
from services.observation.infrastructure.cache import TemporalSearchCache

QUERY = TemporalSearch(210.80227, 54.34895, .1, "SPHEREx-D2", 100)


@pytest.fixture
def legacy_cache(monkeypatch):
    from services.observation.infrastructure import cache as module
    cache = object.__new__(TemporalSearchCache)
    cache.ttl = 21600
    cache.redis = Mock()
    cache.redis.get.return_value = None
    cache.repository = Mock()
    rows = []
    for i in range(2):
        artifact = f"ibe/data/spherex/dr1/level2/test/{i}.fits"
        timestamp = 60805.0 + i
        rows.append(TemporalObservation(
            TemporalObservation.product_key(artifact, QUERY.ra_deg, QUERY.dec_deg,
                                            QUERY.size_deg, QUERY.detector, timestamp),
            artifact, f"https://irsa.ipac.caltech.edu/{artifact}?center={QUERY.ra_deg},{QUERY.dec_deg}d&size=.1",
            timestamp, QUERY.ra_deg, QUERY.dec_deg, QUERY.size_deg, QUERY.detector))
    cache.repository.get_many.return_value = rows
    snapshot = SimpleNamespace(observation_ids=[r.product_id for r in rows],
                               created_at=timezone.now())
    key = cache.query_key(QUERY)
    snapshots = {key: snapshot}
    filter_mock = Mock(side_effect=lambda **kw: SimpleNamespace(first=lambda: snapshots.get(kw["query_key"])))
    monkeypatch.setattr(module.ObservationSearchSnapshot.objects, "filter", filter_mock)
    monkeypatch.setattr(cache, "_redis_key", lambda value: f"test:{value}")
    return cache, snapshot, rows, filter_mock


def test_exact_postgres_hit_preserves_order_ids_and_times(legacy_cache):
    cache, snapshot, rows, _ = legacy_cache
    assert cache.get(QUERY) == rows
    cache.repository.get_many.assert_called_once_with(snapshot.observation_ids)


def test_exact_redis_hit(legacy_cache):
    import json
    cache, snapshot, rows, filter_mock = legacy_cache
    cache.redis.get.return_value = json.dumps(snapshot.observation_ids)
    assert cache.get(QUERY) == rows
    filter_mock.assert_not_called()


@pytest.mark.parametrize("limit", [1, 2, 5])
def test_smaller_limit_does_not_assume_legacy_snapshot_complete(legacy_cache, limit):
    cache, _, _, _ = legacy_cache
    assert cache.get(replace(QUERY, limit=limit)) is None
    cache.repository.get_many.assert_not_called()


@pytest.mark.parametrize("change", [
    {"ra_deg": 304.693508808}, {"dec_deg": 42.4436872991},
    {"size_deg": .05}, {"detector": "SPHEREx-D1"}, {"limit": 99},
])
def test_incompatible_query_cannot_reuse_snapshot(legacy_cache, change):
    cache, _, _, _ = legacy_cache
    assert cache.get(replace(QUERY, **change)) is None
    cache.repository.get_many.assert_not_called()


def test_less_than_limit_is_not_a_completeness_marker(legacy_cache):
    cache, snapshot, _, _ = legacy_cache
    assert len(snapshot.observation_ids) < QUERY.limit
    assert not hasattr(snapshot, "complete")
    assert cache.get(replace(QUERY, limit=1)) is None


def test_expired_snapshot_is_a_miss(legacy_cache):
    cache, snapshot, _, _ = legacy_cache
    snapshot.created_at = timezone.now() - timedelta(seconds=cache.ttl + 1)
    assert cache.get(QUERY) is None
    cache.repository.get_many.assert_not_called()


def test_missing_product_is_a_miss(legacy_cache):
    cache, _, _, _ = legacy_cache
    cache.repository.get_many.return_value = None
    assert cache.get(QUERY) is None


@pytest.mark.parametrize("expired", [False, True])
def test_miss_and_upstream_failure_do_not_return_empty_or_write_cache(legacy_cache, expired):
    cache, snapshot, _, _ = legacy_cache
    query = replace(QUERY, limit=2)
    if expired:
        snapshot.created_at = timezone.now() - timedelta(seconds=cache.ttl + 1)
        query = QUERY
    response = requests.Response()
    response.status_code = 502
    failure = requests.HTTPError("upstream gateway unavailable", response=response)
    catalog = Mock()
    catalog.search.side_effect = failure
    cache.put = Mock()
    with pytest.raises(requests.HTTPError) as caught:
        SearchTemporalObservations(catalog, cache.repository, cache).execute(query)
    assert caught.value is failure
    cache.repository.upsert.assert_not_called()
    cache.put.assert_not_called()


def test_time_and_collection_filters_are_not_supported_parameters():
    # New scientific filters must explicitly extend the query model and cache key.
    with pytest.raises(TypeError):
        replace(QUERY, start_date="2025-01-01")
    with pytest.raises(TypeError):
        replace(QUERY, collections=("spherex_dr1",))
