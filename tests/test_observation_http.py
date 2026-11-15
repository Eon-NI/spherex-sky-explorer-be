"""Observation HTTP contracts with in-memory ports; no PostgreSQL/Redis/MinIO."""
import io
import json
from dataclasses import asdict
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from django.core.cache import cache
from django.db import OperationalError
from drf_spectacular.generators import SchemaGenerator
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.test import APIClient

from services.image_processing.domain.models import SourceProduct
from services.image_processing.infrastructure.clients import ObservationApiResolver
from services.observation.domain.models import Observation, TemporalObservation
from services.observation.presentation import views
from services.observation.presentation.throttling import ObservationRateThrottle

SEARCH = "/api/v1/observations/search/"
TEMPORAL = "/api/v1/observations/temporal/"
SKY = {"ra_deg": 304.693508808, "dec_deg": 42.4436872991, "radius_deg": 1 / 3600,
       "collections": ["spherex_dr1"]}
QUERY = {"ra_deg": 210.80227, "dec_deg": 54.34895, "size_deg": 0.1,
         "detector": "SPHEREx-D2", "limit": 5}
OBS = Observation("ivo://test/product", "spherex_dr1", "https://irsa.ipac.caltech.edu/a.fits",
                  304.7, 42.4, obs_id="test", energy_bandpassname="SPHEREx-D2",
                  s_region="POLYGON ICRS 1 2 3 4 5 6", t_min_mjd=60800, t_max_mjd=60800.01,
                  em_min_m=1.1e-6, em_max_m=1.62e-6, processing_version="l2b-v27-test")
ARTIFACT = "ibe/data/spherex/dr1/level2/test/a.fits"
PRODUCT = TemporalObservation(
    TemporalObservation.product_key(ARTIFACT, 210.80227, 54.34895, 0.1, "SPHEREx-D2", 60805.7),
    ARTIFACT, "https://irsa.ipac.caltech.edu/" + ARTIFACT + "?center=210.80227,54.34895d&size=0.1",
    60805.7, 210.80227, 54.34895, 0.1, "SPHEREx-D2",
)


@pytest.fixture(autouse=True)
def isolated_throttling(settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                                    "LOCATION": "observation-http-tests"}}
    settings.ALLOWED_HOSTS = ["testserver"]
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def ports(monkeypatch):
    class Repository:
        def __init__(self, key):
            self.key = key
            self.saved = {}
        def upsert(self, rows):
            changed = sum(self.saved.get(getattr(row, self.key)) != row for row in rows)
            self.saved.update((getattr(row, self.key), row) for row in rows)
            return changed
        def get(self, key):
            return self.saved.get(key)
    class Snapshot:
        def __init__(self, repo):
            self.repo, self.ids = repo, None
        def get(self, query):
            return None if self.ids is None else [self.repo.saved[key] for key in self.ids]
        def put(self, query, rows):
            self.ids = [getattr(row, self.repo.key) for row in rows]
        def invalidate_all(self):
            self.ids = None
    sia_repo, tap_repo = Repository("publisher_did"), Repository("product_id")
    sia_cache, tap_cache = Snapshot(sia_repo), Snapshot(tap_repo)
    monkeypatch.setattr(views, "DjangoObservationRepository", lambda: sia_repo)
    monkeypatch.setattr(views, "DjangoTemporalObservationRepository", lambda: tap_repo)
    monkeypatch.setattr(views, "ObservationSearchCache", lambda repo: sia_cache)
    monkeypatch.setattr(views, "TemporalSearchCache", lambda repo: tap_cache)
    with patch.object(views.IrsaSiaCatalog, "search", return_value=[OBS]) as sia, \
         patch.object(views.IrsaTapCatalog, "search", return_value=[PRODUCT]) as tap:
        yield sia, tap, tap_repo


def test_sia_fresh_cached_identity_and_metadata(client, ports):
    sia, _, _ = ports
    sia.return_value = [OBS, OBS]
    fresh = client.get(SEARCH, SKY)
    cached = client.get(SEARCH, SKY)
    assert fresh.status_code == cached.status_code == 200
    assert fresh.json() == cached.json()
    assert fresh.json()["count"] == 1
    assert fresh.json()["results"][0] == json.loads(json.dumps(asdict(OBS)))
    sia.assert_called_once()


def test_valid_fov_and_collections_encoding(client, ports):
    payload = dict(SKY, fov_deg=0.002, collections=["spherex_qr3", "spherex_dr1"])
    payload.pop("radius_deg")
    response = client.get(SEARCH, payload)
    assert response.status_code == 200
    assert response.json()["geometry"] == {"type": "circle", "radius_deg": 0.001}
    assert ports[0].call_args.args[0].collections == ("spherex_qr3", "spherex_dr1")


@pytest.mark.parametrize("url,payload,extra", [
    (SEARCH, SKY, {"detector": "SPHEREx-D2"}),
    (SEARCH, SKY, {"start_date": "2025-01-01"}),
    (SEARCH, SKY, {"page": 1}),
    (TEMPORAL, QUERY, {"collections": "spherex_dr1"}),
    (TEMPORAL, QUERY, {"end_date": "bad"}),
    (TEMPORAL, QUERY, {"limit": [1, 2]}),
])
def test_unsupported_or_repeated_parameters(client, ports, url, payload, extra):
    assert client.get(url, dict(payload, **extra)).status_code == 400
    ports[0].assert_not_called()
    ports[1].assert_not_called()


@pytest.mark.parametrize("change", [
    {"ra_deg": -1}, {"ra_deg": 360}, {"dec_deg": -91}, {"dec_deg": 91},
    {"ra_deg": "nan"}, {"dec_deg": "inf"}, {"ra_deg": "abc"},
    {"radius_deg": 0}, {"radius_deg": 1.1}, {"fov_deg": 0.01},
    {"collections": ["spherex_qr2"]},
])
def test_invalid_sia_parameters(client, ports, change):
    assert client.get(SEARCH, dict(SKY, **change)).status_code == 400
    ports[0].assert_not_called()


def test_ra_below_360_and_poles(client, ports):
    assert client.get(SEARCH, dict(SKY, ra_deg=359.99999999, dec_deg=90)).status_code == 200


@pytest.mark.parametrize("change", [
    {"ra_deg": 360}, {"dec_deg": 91}, {"ra_deg": "nan"}, {"size_deg": 0},
    {"size_deg": 2}, {"detector": "SPHEREx-D7"}, {"detector": "D2'; DROP TABLE x"},
    {"limit": 0}, {"limit": -1}, {"limit": 501}, {"limit": 101}, {"limit": "null"},
])
def test_invalid_temporal_parameters(client, ports, change):
    assert client.get(TEMPORAL, dict(QUERY, **change)).status_code == 400
    ports[1].assert_not_called()


@pytest.mark.parametrize("url,payload,index", [(SEARCH, SKY, 0), (TEMPORAL, QUERY, 1)])
def test_empty_search_is_cached(client, ports, url, payload, index):
    ports[index].return_value = []
    for _ in range(2):
        response = client.get(url, payload)
        assert response.status_code == 200
        assert response.json()["count"] == 0 and response.json()["results"] == []
    ports[index].assert_called_once()


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), RuntimeError("upstream failure")])
def test_temporal_provider_failures_are_502(client, ports, failure):
    ports[1].side_effect = failure
    response = client.get(TEMPORAL, QUERY)
    assert response.status_code == 502
    assert response.json() == {"detail": "IRSA temporal discovery unavailable"}


def test_sia_provider_failure_is_502(client, ports):
    ports[0].side_effect = RuntimeError("provider")
    assert client.get(SEARCH, SKY).status_code == 502


def test_local_infrastructure_failure_is_503(client, ports):
    with patch.object(views, "DjangoObservationRepository", side_effect=OperationalError("offline")):
        assert client.get(SEARCH, SKY).status_code == 503
    ports[0].assert_not_called()


def test_temporal_search_detail_and_be02_resolver_contract(client, ports):
    found = client.get(TEMPORAL, QUERY)
    assert found.status_code == 200
    product = found.json()["results"][0]
    detail = client.get(TEMPORAL + product["product_id"] + "/")
    assert detail.status_code == 200 and detail.json() == product
    assert asdict(SourceProduct(**detail.json())) == product
    with patch("services.image_processing.infrastructure.clients.urlopen",
               return_value=io.BytesIO(detail.content)) as urlopen:
        resolved = ObservationApiResolver().get(product["product_id"])
    assert asdict(resolved) == product
    assert urlopen.call_args.args[0].full_url.endswith(TEMPORAL + product["product_id"] + "/")


@pytest.mark.parametrize("identifier,status", [("not-a-hash", 400), ("a" * 64, 404)])
def test_detail_bad_or_missing_product(client, ports, identifier, status):
    assert client.get(TEMPORAL + identifier + "/").status_code == status
    ports[1].assert_not_called()


def test_detail_unknown_parameters_and_database_failure(client, ports):
    assert client.get(TEMPORAL + PRODUCT.product_id + "/", {"extra": 1}).status_code == 400
    with patch.object(ports[2], "get", side_effect=OperationalError("offline")):
        assert client.get(TEMPORAL + PRODUCT.product_id + "/").status_code == 503


def test_be02_resolver_unknown_product():
    error = HTTPError("http://observation/product", 404, "missing", {}, None)
    with patch("services.image_processing.infrastructure.clients.urlopen", side_effect=error):
        assert ObservationApiResolver().get("a" * 64) is None


@pytest.mark.parametrize("url,payload", [(SEARCH, SKY), (TEMPORAL, QUERY),
                                         (TEMPORAL + PRODUCT.product_id + "/", {})])
def test_redis_outage_fails_closed_before_irsa(client, ports, url, payload):
    with patch.object(ObservationRateThrottle.cache, "get", side_effect=RedisConnectionError("offline")):
        response = client.get(url, payload)
    assert response.status_code == 503
    assert "Rate limiting" in response.json()["detail"]
    ports[0].assert_not_called()
    ports[1].assert_not_called()


def test_normal_throttling_remains_429(client, ports):
    for _ in range(10):
        assert client.get(TEMPORAL, QUERY).status_code == 200
    response = client.get(TEMPORAL, QUERY)
    assert response.status_code == 429 and "Retry-After" in response
    ports[1].assert_called_once()  # Cached calls also consume the rate quota.


def test_openapi_matches_envelopes_and_errors():
    schema = SchemaGenerator().get_schema(request=None, public=True)
    ids = [operation["operationId"] for path, methods in schema["paths"].items()
           if "/observations/" in path for operation in methods.values()]
    assert len(ids) == len(set(ids))
    assert set(ids) == {"observations_search", "observations_temporal_search", "observations_temporal_detail"}
    components = schema["components"]["schemas"]
    def resolve(node):
        return components[node["$ref"].rsplit("/", 1)[-1]] if "$ref" in node else node
    for path in (SEARCH, TEMPORAL):
        operation = schema["paths"][path]["get"]
        response = resolve(operation["responses"]["200"]["content"]["application/json"]["schema"])
        assert response["type"] == "object"
        assert {"count", "results"} <= set(response["properties"])
        assert response["properties"]["results"]["type"] == "array"
        assert {"400", "429", "502", "503"} <= set(operation["responses"])
        if path == SEARCH:
            assert "geometry" in response["properties"]
    detail = schema["paths"][TEMPORAL + "{product_id}/"]["get"]
    assert {"200", "400", "404", "429", "503"} <= set(detail["responses"])


@pytest.mark.parametrize("position", [(210.80227, 54.34895), (304.693508808, 42.4436872991), (0, 90)])
@pytest.mark.parametrize("available", [True, False])
def test_temporal_regions_transport_fallback_and_empty_cache(client, ports, position, available):
    from dataclasses import replace
    from services.observation.infrastructure.tap import TapUnavailable
    from services.observation.infrastructure.temporal import IrsaSiaTemporalCatalog
    ra, dec = position
    product = replace(PRODUCT, ra_deg=ra, dec_deg=dec,
        product_id=TemporalObservation.product_key(PRODUCT.artifact_uri, ra, dec, .1, PRODUCT.detector, PRODUCT.time_bounds_lower),
        cutout_url=f"https://irsa.ipac.caltech.edu/{PRODUCT.artifact_uri}?center={ra},{dec}d&size=0.1")
    ports[1].side_effect = TapUnavailable("bounded TAP timeout")
    with patch.object(IrsaSiaTemporalCatalog, "search", return_value=[product] if available else []) as fallback:
        payload = dict(QUERY, ra_deg=ra, dec_deg=dec)
        first = client.get(TEMPORAL, payload)
        second = client.get(TEMPORAL, payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["count"] == int(available)
    fallback.assert_called_once()
    assert fallback.call_args.args[0].ra_deg == ra
    assert fallback.call_args.args[0].dec_deg == dec


def test_both_catalogs_unavailable_are_not_cached_as_empty(client, ports, caplog):
    import requests
    from services.observation.infrastructure.tap import TapUnavailable
    from services.observation.infrastructure.temporal import IrsaSiaTemporalCatalog
    ports[1].side_effect = TapUnavailable("bounded TAP timeout")
    upstream = requests.Response()
    upstream.status_code = 502
    with patch.object(IrsaSiaTemporalCatalog, "search", side_effect=requests.HTTPError("SIA gateway", response=upstream)) as fallback:
        for _ in range(2):
            response = client.get(TEMPORAL, QUERY)
            assert response.status_code == 502
            assert response.json() == {"detail": "IRSA temporal discovery unavailable"}
    assert fallback.call_count == 2
    assert ports[2].saved == {}
    record = next(r for r in caplog.records if r.message == "Temporal discovery unavailable")
    assert record.exception_type == "HTTPError" and record.http_status == 502
