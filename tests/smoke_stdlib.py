"""Dependency-free checks for domain, ADQL construction and download bounds."""
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest

from services.observation.domain.models import SkySearch, TemporalSearch, Observation
from services.observation.application.use_cases import SearchObservations
from services.image_processing.domain.models import CutoutRequest
from services.image_processing.domain.security import validate_cutout_url
from services.image_processing.infrastructure.downloader import IrsaCutoutDownloader


class SmokeTests(unittest.TestCase):
    def test_cache_hit_avoids_catalog(self):
        row = Observation("did", "spherex_qr3", "https://irsa.ipac.caltech.edu/a.fits", 1, 2)
        class Catalog:
            def search(self, query):
                raise AssertionError("IRSA should not be queried")
        class Cache:
            def get(self, query):
                return [row]
        self.assertEqual(SearchObservations(Catalog(), cache=Cache()).execute(SkySearch(1, 2, 0.1)), [row])

    def test_domain_rejects_nonfinite_and_injection(self):
        with self.assertRaises(ValueError):
            SkySearch(float("nan"), 0, 0.1)
        with self.assertRaises(ValueError):
            TemporalSearch(1, 2, 0.1, "SPHEREx-D2';DROP")
        with self.assertRaises(ValueError):
            CutoutRequest("bad", 1, 2, 0.1)

    def test_verified_cutout_url(self):
        artifact = "ibe/data/spherex/dr1/level2/x/a.fits"
        url = "https://irsa.ipac.caltech.edu/" + artifact + "?center=210.80227,54.34895d&size=0.1"
        self.assertEqual(validate_cutout_url(url, artifact, 210.80227, 54.34895, 0.1), url)
        with self.assertRaises(ValueError):
            validate_cutout_url(url.replace("irsa.ipac.caltech.edu", "evil.example"),
                                artifact, 210.80227, 54.34895, 0.1)

    def test_tap_builds_bounded_documented_adql(self):
        # The adapter only needs Irsa when executing; stub it to test pure ADQL construction.
        irsa = types.ModuleType("astroquery.ipac.irsa")
        irsa.IrsaClass = object
        irsa.conf = types.SimpleNamespace(timeout=60)
        old = {name: sys.modules.get(name) for name in ("astroquery", "astroquery.ipac", "astroquery.ipac.irsa")}
        sys.modules["astroquery"] = types.ModuleType("astroquery")
        sys.modules["astroquery.ipac"] = types.ModuleType("astroquery.ipac")
        sys.modules["astroquery.ipac.irsa"] = irsa
        try:
            from services.observation.infrastructure.tap import IrsaTapCatalog
            sql = IrsaTapCatalog.build_adql(TemporalSearch(210.80227, 54.34895, 0.1, "SPHEREx-D2", 3))
            self.assertIn("SELECT TOP 3", sql)
            self.assertIn("JOIN spherex.plane p ON a.planeid = p.planeid", sql)
            self.assertIn("ORDER BY p.time_bounds_lower", sql)
        finally:
            for name, previous in old.items():
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous

    def test_streaming_downloader_checks_fits_and_byte_cap(self):
        class Response(io.BytesIO):
            status = 200
            headers = {}
        class Opener:
            def open(self, request, timeout):
                return Response(b"SIMPLE  =                    T" + b" " * 2900)
        downloader = IrsaCutoutDownloader(retries=1)
        downloader.opener = Opener()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.fits"
            self.assertGreater(downloader.download("https://irsa.ipac.caltech.edu/a", path, 4096), 2880)
            with self.assertRaises(ValueError):
                downloader.download("https://irsa.ipac.caltech.edu/a", path, 100)


if __name__ == "__main__":
    unittest.main()
