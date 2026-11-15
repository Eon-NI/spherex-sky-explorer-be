"""Regression checks for scientific nulls, raw quality values and tile routing."""
import unittest
import threading
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

import numpy as np
import requests
from astropy.io import fits
from rest_framework.test import APIRequestFactory

from services.spectral.application.use_cases import extract_spectrum, preview_png
from services.spectral.domain.models import SpectrumRequest
from services.spectral.infrastructure.hips import CubeStore, tile_number, tile_url
from services.jobs.presentation.views import JobCollectionView
from services.spectral.presentation.views import SpectrumRequestView


class SpectralContractTests(unittest.TestCase):
    def make_cube(self):
        image = np.ones((102, 16, 16), dtype="float32")
        hits = np.ones((102, 16, 16), dtype="int32")
        flags = np.zeros((102, 16, 16), dtype="int32")
        image[50] = np.nan
        image[51] = np.nan
        hits[50] = 0
        hits[51] = 0
        flags[2] = 8
        header = fits.Header()
        header["BUNIT"] = "MJy/sr"
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CTYPE3"] = "WAVE"
        header["CUNIT1"] = "deg"
        header["CUNIT2"] = "deg"
        header["CUNIT3"] = "m"
        header["CRPIX1"] = 8
        header["CRPIX2"] = 8
        header["CRPIX3"] = 1
        header["CRVAL1"] = 60
        header["CRVAL2"] = 50
        header["CRVAL3"] = 0.75e-6
        header["CDELT1"] = -0.01
        header["CDELT2"] = 0.01
        header["CDELT3"] = 0.01e-6
        table = fits.BinTableHDU.from_columns([fits.Column(name="CHANNEL", format="I",
                                                            array=np.arange(102))],
                                              name="SPECTRAL_CHANNELS")
        wave = fits.BinTableHDU.from_columns([fits.Column(name="DUMMY", format="I",
                                                           array=[1])], name="WCS-WAVE")
        return fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(image, header, name="IMAGE"),
                             fits.ImageHDU(hits, name="NHITS"),
                             fits.ImageHDU(flags, name="FLAGS"), table, wave])

    def test_scientific_quality_and_blank_channels(self):
        channels = [{"channel_index": index} for index in range(102)]
        with self.make_cube() as hdul:
            result = extract_spectrum(hdul, 445, 60, 50, channels)
            self.assertEqual(result["unit"], "MJy/sr")
            self.assertEqual(len(result["points"]), 102)
            self.assertIsNone(result["points"][50]["flux"])
            self.assertIsNone(result["points"][51]["flux"])
            self.assertEqual(result["points"][2]["flags"], 8)
            self.assertEqual(result["points"][2]["nhits"], 1)
            self.assertAlmostEqual(result["points"][0]["wavelength_um"], 0.75)
            with self.assertRaises(ValueError):
                preview_png(hdul, 50, 60, 50, 0.1)

    def test_documented_order_five_example(self):
        self.assertEqual(tile_number(60.8109955, 51.3404038), 445)
        self.assertTrue(tile_url(445).endswith("Norder5/Dir0/Npix445_cube.fits"))

    def test_request_rejects_non_finite_coordinates(self):
        with self.assertRaises(ValueError):
            SpectrumRequest(float("nan"), 51, 0.5)
        with self.assertRaises(ValueError):
            SpectrumRequest(360, 51, 0.5)

    def test_jobs_keeps_distinct_image_and_spectral_throttles(self):
        for task, expected in (
            ("eon.image_processing.create_cutout", "cutout_submit"),
            ("eon.spectral.extract_spectrum", "spectral_submit"),
            ("eon.spectral.prepare_cube", "spectral_submit"),
            ([], "cutout_submit"),
        ):
            view = JobCollectionView()
            view.request = SimpleNamespace(data={"task": task})
            self.assertEqual(len(view.get_throttles()), 1)
            self.assertEqual(view.throttle_scope, expected)

    def test_jobs_429_is_not_reported_as_gateway_error(self):
        headers = Message()
        headers["Retry-After"] = "12"
        error = HTTPError("http://jobs/api/v1/jobs/", 429, "Too Many Requests", headers, None)
        request = APIRequestFactory().post("/api/v1/spectral/spectra/",
                                           {"ra_deg": 200, "dec_deg": 42, "fov_deg": 0.5},
                                           format="json")
        with (patch.object(SpectrumRequestView, "throttle_classes", []),
              patch("services.spectral.presentation.views.JobsClient.submit", side_effect=error)):
            response = SpectrumRequestView.as_view()(request)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "12")

    def test_irsa_stream_resumes_after_read_timeout(self):
        payload = b"SIMPLE" + b" " * 2874

        class Response:
            is_redirect = False
            def __init__(self, status, headers, chunks):
                self.status_code, self.headers, self.chunks = status, headers, chunks
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def raise_for_status(self):
                pass
            def iter_content(self, _size):
                yield from self.chunks

        def broken_stream():
            yield payload[:1000]
            raise requests.ConnectionError("read timed out")

        calls = []
        responses = [Response(200, {}, []),
                     Response(200, {"Content-Length": "2880"}, broken_stream()),
                     Response(206, {"Content-Range": "bytes 1000-2879/2880"},
                              [payload[1000:]])]
        def get(_url, **kwargs):
            calls.append(kwargs.get("headers"))
            return responses.pop(0)

        store = CubeStore.__new__(CubeStore)
        store.bucket = "test"
        uploads = []
        store.client = SimpleNamespace(upload_file=lambda path, *_args, **_kwargs:
                                       uploads.append(Path(path).read_bytes()))
        store.exists = lambda _key: False
        with (patch("services.spectral.infrastructure.hips.requests.get", side_effect=get),
              patch("services.spectral.infrastructure.hips.time.sleep"),
              patch("services.spectral.infrastructure.hips.settings",
                    SimpleNamespace(EON_MAX_SPECTRAL_CUBE_BYTES=4000,
                                    EON_SPECTRAL_CACHE_MAX_BYTES=0))):
            directory, path = store.local_cube(445)
            try:
                self.assertEqual(path.read_bytes(), payload)
                self.assertEqual(uploads, [payload])
                self.assertEqual(calls, [{"Range": "bytes=0-2879"}, {},
                                         {"Range": "bytes=1000-"}])
            finally:
                directory.cleanup()

    def test_parallel_ranges_assemble_one_verified_fits(self):
        payload = b"SIMPLE" + b" " * (3 * 2880 - 6)
        etag = '"version-1"'

        class Response:
            status_code = 206
            def __init__(self, start, end):
                self.headers = {"Content-Range": f"bytes {start}-{end}/{len(payload)}",
                                "ETag": etag}
                self.start, self.end = start, end
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def raise_for_status(self):
                pass
            def iter_content(self, _size):
                yield payload[self.start:self.end + 1]

        ranges = []
        def get(_url, **kwargs):
            start, end = map(int, kwargs["headers"]["Range"].removeprefix("bytes=").split("-"))
            ranges.append((start, end))
            return Response(start, end)

        store = CubeStore.__new__(CubeStore)
        store.bucket = "test"
        uploads = []
        store.client = SimpleNamespace(upload_file=lambda path, *_args, **_kwargs:
                                       uploads.append(Path(path).read_bytes()))
        store.exists = lambda _key: False
        callback_threads = []
        with (patch("services.spectral.infrastructure.hips.requests.get", side_effect=get),
              patch("services.spectral.infrastructure.hips.settings",
                    SimpleNamespace(EON_MAX_SPECTRAL_CUBE_BYTES=9000,
                                    EON_SPECTRAL_DOWNLOAD_WORKERS=2,
                                    EON_SPECTRAL_CACHE_MAX_BYTES=0))):
            directory, path = store.local_cube(445, lambda _value: callback_threads.append(threading.get_ident()))
            try:
                self.assertEqual(path.read_bytes(), payload)
                self.assertEqual(uploads, [payload])
                self.assertCountEqual(ranges, [(0, 2879), (2880, 5759), (5760, 8639)])
                self.assertTrue(callback_threads)
                self.assertEqual(set(callback_threads), {threading.get_ident()})
            finally:
                directory.cleanup()

    def test_repeated_spectra_reuse_one_local_tile(self):
        import tempfile
        payload = b"SIMPLE" + b" " * 2874
        store = CubeStore.__new__(CubeStore)
        downloads = []

        def download(_tile, _progress):
            downloads.append(1)
            directory = tempfile.TemporaryDirectory()
            path = Path(directory.name) / "cube.fits"
            path.write_bytes(payload)
            return directory, path

        store._local_cube_uncached = download
        with (tempfile.TemporaryDirectory() as cache_dir,
              patch("services.spectral.infrastructure.hips.settings",
                    SimpleNamespace(EON_MAX_SPECTRAL_CUBE_BYTES=4000,
                                    EON_SPECTRAL_CACHE_MAX_BYTES=8000,
                                    EON_SPECTRAL_CACHE_DIR=cache_dir))):
            first_dir, first_path = store.local_cube(445)
            first_dir.cleanup()
            second_dir, second_path = store.local_cube(445)
            try:
                self.assertEqual(second_path.read_bytes(), payload)
                self.assertEqual(len(downloads), 1)
            finally:
                second_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
