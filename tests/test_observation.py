from unittest.mock import patch
import pytest
from services.observation.application.use_cases import SearchObservations, SyncObservations
from services.observation.domain.models import Observation, SkySearch


@pytest.mark.parametrize("ra,dec,radius", [(360, 0, 0.1), (0, 91, 0.1), (0, 0, 0), (0, 0, 2)])
def test_rejects_invalid_search(ra, dec, radius):
    with pytest.raises(ValueError):
        SkySearch(ra, dec, radius)


def test_search_uses_injected_catalog():
    expected = [Observation("did", "spherex_dr1", "https://example.org/a.fits", 1, 2)]
    class Catalog:
        def search(self, query):
            assert query == SkySearch(1, 2, 0.01)
            return expected
    assert SearchObservations(Catalog()).execute(SkySearch(1, 2, 0.01)) == expected


def test_sync_upserts_catalog_results():
    expected = [Observation("did", "spherex_dr1", "https://example.org/a.fits", 1, 2)]
    class Catalog:
        def search(self, query):
            return expected
    class Repository:
        def upsert(self, observations):
            assert observations == expected
            return len(observations)
    assert SyncObservations(Catalog(), Repository()).execute(SkySearch(1, 2, 0.01)) == 1


def test_irsa_adapter_uses_documented_sia_query():
    from services.observation.infrastructure.irsa import IrsaSiaCatalog
    rows = [{"obs_publisher_did": "did", "obs_collection": "spherex_dr1",
             "access_url": "https://irsa.ipac.caltech.edu/example.fits", "s_ra": 1, "s_dec": 2}]
    with patch("services.observation.infrastructure.irsa.Irsa.query_sia", return_value=rows) as query:
        result = IrsaSiaCatalog().search(SkySearch(1, 2, 1 / 3600))
    assert result[0].publisher_did == "did"
    assert query.call_args.kwargs["collection"] == ["spherex_dr1", "spherex_qr3"]
