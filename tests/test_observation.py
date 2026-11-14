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


SIA_XML = b'''<VOTABLE version="1.3" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">
<RESOURCE type="results"><INFO name="QUERY_STATUS" value="OK"/>
<TABLE><FIELD name="obs_publisher_did" datatype="char" arraysize="*"/>
<FIELD name="obs_collection" datatype="char" arraysize="*"/>
<FIELD name="access_url" datatype="char" arraysize="*"/>
<FIELD name="t_min" datatype="double" unit="d"/>
<FIELD name="t_max" datatype="double" unit="d"/>
<DATA><TABLEDATA><TR><TD>did</TD><TD>spherex_dr1</TD>
<TD>https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/example.fits</TD>
<TD>60805.7</TD><TD>60805.8</TD></TR></TABLEDATA></DATA></TABLE></RESOURCE></VOTABLE>'''


class SiaResponse:
    is_redirect = False

    def __init__(self, content=SIA_XML):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, _):
        yield self.content


def test_irsa_adapter_uses_verified_direct_sia_query():
    from services.observation.infrastructure.irsa import IrsaSiaCatalog
    with patch("services.observation.infrastructure.irsa.requests.get", return_value=SiaResponse()) as get:
        result = IrsaSiaCatalog().search(SkySearch(1, 2, 1 / 3600))
    assert len(result) == 2
    assert result[0].publisher_did == "did"
    assert get.call_count == 2
    assert [call.kwargs["params"]["COLLECTION"] for call in get.call_args_list] == ["spherex_qr3", "spherex_dr1"]
    assert get.call_args.kwargs["params"]["POS"] == "CIRCLE 1 2 0.000277777777778"
    assert get.call_args.kwargs["allow_redirects"] is False


def test_irsa_adapter_rejects_overflow_and_bad_time_units():
    from services.observation.infrastructure.irsa import _read_votable
    with pytest.raises(ValueError, match="QUERY_STATUS"):
        _read_votable(SIA_XML.replace(b'value="OK"', b'value="OVERFLOW"'))
    with pytest.raises(ValueError, match="MJD days"):
        _read_votable(SIA_XML.replace(b'unit="d"', b'unit="s"'))
