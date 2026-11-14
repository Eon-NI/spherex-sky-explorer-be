import json
import pytest
from services.observation.domain.models import COLLECTIONS


def test_configured_regions_queries_all_current_collections(monkeypatch):
    from services.observation.infrastructure.tasks import configured_regions
    monkeypatch.setenv("IRSA_SYNC_REGIONS", json.dumps([
        {"ra_deg": 210.80227, "dec_deg": 54.34895, "radius_deg": 0.1}
    ]))
    regions = configured_regions()
    assert len(regions) == 1
    assert regions[0].collections == COLLECTIONS
    assert set(COLLECTIONS) == {"spherex_qr3", "spherex_qr3_deep", "spherex_dr1", "spherex_dr1_deep"}


def test_configured_regions_rejects_invalid_coordinates(monkeypatch):
    from services.observation.infrastructure.tasks import configured_regions
    monkeypatch.setenv("IRSA_SYNC_REGIONS", '[{"ra_deg":360,"dec_deg":0,"radius_deg":0.1}]')
    with pytest.raises(ValueError):
        configured_regions()
