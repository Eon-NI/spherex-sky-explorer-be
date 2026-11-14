from pathlib import Path
from unittest.mock import patch
import pytest
from services.observation.domain.models import SkySearch, TemporalSearch
from services.observation.infrastructure.tap import IrsaTapCatalog
from services.image_processing.domain.models import CutoutRequest
from services.image_processing.domain.security import validate_cutout_url


def test_tap_adql_and_chronological_normalization():
    query = TemporalSearch(210.80227, 54.34895, 0.1, "SPHEREx-D2", limit=3)
    url = "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/x/a.fits?center=210.80227,54.34895d&size=0.1"
    class Result:
        def to_table(self):
            return [{"uri": url, "artifact_uri": "ibe/data/spherex/dr1/level2/x/a.fits",
                     "time_bounds_lower": 60002.0},
                    {"uri": url, "artifact_uri": "ibe/data/spherex/dr1/level2/x/b.fits",
                     "time_bounds_lower": 60001.0}]
    with patch("services.observation.infrastructure.tap.Irsa.query_tap", return_value=Result()) as run:
        results = IrsaTapCatalog().search(query)
    adql = run.call_args.args[0]
    assert "SELECT TOP 3" in adql
    assert "spherex.artifact" in adql and "spherex.plane" in adql
    assert "SPHEREx-D2" in adql and "210.80227" in adql
    assert [item.time_bounds_lower for item in results] == [60001.0, 60002.0]
    assert run.call_args.kwargs["maxrec"] == 3


@pytest.mark.parametrize("detector", ["SPHEREx-D0", "SPHEREx-D7", "D2'; DROP TABLE x"])
def test_tap_rejects_unsupported_detector(detector):
    with pytest.raises(ValueError):
        TemporalSearch(210.80227, 54.34895, 0.1, detector)


def test_sia_collection_validation():
    with pytest.raises(ValueError):
        SkySearch(1, 2, 0.1, ("spherex_qr2",))


def test_cutout_url_rejects_ssrf_and_mismatched_parameters():
    artifact = "ibe/data/spherex/dr1/level2/x/a.fits"
    valid = "https://irsa.ipac.caltech.edu/" + artifact + "?center=210.80227,54.34895d&size=0.1"
    assert validate_cutout_url(valid, artifact, 210.80227, 54.34895, 0.1) == valid
    for url in [valid.replace("irsa.ipac.caltech.edu", "evil.example"),
                valid.replace("https://", "http://"),
                valid.replace("size=0.1", "size=0.2"),
                valid.replace("/level2/x/", "/level2/../")]:
        with pytest.raises(ValueError):
            validate_cutout_url(url, artifact, 210.80227, 54.34895, 0.1)


def test_cutout_request_key_is_deterministic():
    a = CutoutRequest("a" * 64, 210.80227, 54.34895, 0.1)
    assert a.idempotency_key == CutoutRequest("a" * 64, 210.80227, 54.34895, 0.1).idempotency_key
    assert a.idempotency_key != CutoutRequest("a" * 64, 210.80227, 54.34895, 0.2).idempotency_key


def test_fits_reader_spatial_wcs_and_missing_flags(tmp_path: Path):
    import numpy as np
    from astropy.io import fits
    from astropy.wcs import WCS
    from services.image_processing.infrastructure.fits_reader import AstropyFitsReader
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [2, 2]
    wcs.wcs.cdelt = [-0.01, 0.01]
    wcs.wcs.crval = [210.80227, 54.34895]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    image = fits.ImageHDU(np.array([[1., 2., 3.], [4., 5., 6.], [7., 8., 9.]], dtype="f4"), name="IMAGE")
    image.header.update(wcs.to_header())
    image.header["BUNIT"] = "MJy / sr"
    path = tmp_path / "small.fits"
    fits.HDUList([fits.PrimaryHDU(), image]).writeto(path)
    metadata = AstropyFitsReader().inspect(path, 210.80227, 54.34895)
    assert metadata.image_shape == (3, 3)
    assert metadata.flags_available is False
    assert metadata.image_statistics["mean"] == 5
    assert abs(metadata.pixel_position_xy[0] - 1) < 1e-6
    assert metadata.image_unit == "MJy / sr"


def test_tap_empty_and_external_error_are_distinct():
    query = TemporalSearch(210.80227, 54.34895, 0.1, "SPHEREx-D2", limit=3)
    class Empty:
        def to_table(self):
            return []
    with patch("services.observation.infrastructure.tap.Irsa.query_tap", return_value=Empty()):
        assert IrsaTapCatalog().search(query) == []
    with patch("services.observation.infrastructure.tap.Irsa.query_tap", side_effect=TimeoutError):
        with pytest.raises(RuntimeError, match="unavailable"):
            IrsaTapCatalog().search(query)
