"""Opt-in network tests; run with EON_RUN_LIVE_IRSA=1 pytest tests/integration -m integration."""
import os
from pathlib import Path
import pytest
from services.observation.domain.models import SkySearch, TemporalSearch
from services.observation.infrastructure.irsa import IrsaSiaCatalog
from services.observation.infrastructure.tap import IrsaTapCatalog
from services.image_processing.infrastructure.downloader import IrsaCutoutDownloader
from services.image_processing.infrastructure.fits_reader import AstropyFitsReader
from services.image_processing.domain.security import validate_cutout_url

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(os.getenv("EON_RUN_LIVE_IRSA") != "1", reason="Live IRSA opt-in")]


def test_sia_official_tutorial_coordinate():
    results = IrsaSiaCatalog().search(SkySearch(304.693508808, 42.4436872991, 1 / 3600))
    assert results
    assert all(item.collection in {"spherex_dr1", "spherex_qr3"} for item in results)
    assert all(item.access_url.startswith("https://irsa.ipac.caltech.edu/") for item in results)


def test_tap_cutout_fits_and_wcs(tmp_path: Path):
    query = TemporalSearch(210.80227, 54.34895, 0.1, "SPHEREx-D2", limit=10)
    results = IrsaTapCatalog().search(query)
    assert results
    assert [x.time_bounds_lower for x in results] == sorted(x.time_bounds_lower for x in results)
    item = results[0]
    validate_cutout_url(item.cutout_url, item.artifact_uri, query.ra_deg, query.dec_deg, query.size_deg)
    path = tmp_path / "real-cutout.fits"
    IrsaCutoutDownloader().download(item.cutout_url, path, 128 * 1024 * 1024)
    metadata = AstropyFitsReader().inspect(path, query.ra_deg, query.dec_deg)
    assert metadata.image_shape[0] > 0 and metadata.image_shape[1] > 0
    assert metadata.flags_available
    assert metadata.pixel_position_xy is not None
    assert metadata.wavelength_um is not None
    assert metadata.bandwidth_um is not None
