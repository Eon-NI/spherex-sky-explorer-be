"""Deterministic scientific fixtures; no network, broker, databases or S3 service."""
import io
import json
import shutil
from dataclasses import replace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
from PIL import Image

from services.image_processing.application.timelapse import ProcessTimelapse
from services.image_processing.domain.models import ImageReference, SourceProduct
from services.image_processing.domain.timelapse import TimelapseRequest
from services.image_processing.infrastructure.storage import S3ScientificObjectStore
from services.image_processing.infrastructure.timelapse import (
    FitsSequenceRenderer,
    acquisition_time,
    intensity_pixels,
    target_grid,
)


def request(ids=("a"*64,), **kwargs):
    return TimelapseRequest("test-job", ids, 210.80227, 54.34895, 0.1, **kwargs)


def product(pid="a"*64, **changes):
    artifact = f"ibe/data/spherex/dr1/level2/test/l2b-v27-2026-198/2/{pid}.fits"
    result = SourceProduct(pid, artifact,
                           "https://irsa.ipac.caltech.edu/" + artifact + "?center=210.80227,54.34895d&size=0.1",
                           210.80227, 54.34895, 0.1, "SPHEREx-D2", 60805.7)
    return replace(result, **changes)


def make_fits(path, when="2025-05-01T00:00:00", obsid="obs-one", angle=0, data=None):
    grid, _shape = target_grid(request())
    spatial = WCS(grid.to_header())
    spatial.wcs.crpix = [40, 40]
    theta = np.deg2rad(angle)
    spatial.wcs.pc = [[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]]
    y, x = np.indices((79, 79))
    ra, dec = spatial.pixel_to_world_values(x, y)
    xx, yy = grid.world_to_pixel_values(ra, dec)
    if data is None:
        data = 1 + 10 * np.exp(-((xx-32)**2 + (yy-26)**2)/6)
    header = spatial.to_header()
    header.update({"BUNIT": "MJy/sr", "DETECTOR": 2, "OBSID": obsid, "TIMESYS": "UTC",
                   "DATE-OBS": when, "MJD-OBS": float(Time(when, format="isot", scale="utc").mjd)})
    fits.HDUList([fits.PrimaryHDU(header=fits.Header({"VERSION": "synthetic-test"})),
                  fits.ImageHDU(data, header=header, name="IMAGE"),
                  fits.ImageHDU(np.zeros(data.shape, dtype=np.int32), name="FLAGS"),
                  fits.ImageHDU(np.ones(data.shape), name="VARIANCE")]).writeto(path)
    return path


class Store:
    def __init__(self, fail_at=None):
        self.objects, self.types, self.calls = {}, {}, []
        self.fail_at = fail_at
    def upload_file(self, path, reference):
        self.calls.append(reference.object_key)
        if len(self.calls) == self.fail_at:
            raise OSError("simulated upload failure")
        self.objects[reference.object_key] = path.read_bytes()
        self.types[reference.object_key] = reference.content_type


def pipeline(paths, products, store=None):
    lookup = {p.product_id: p for p in products}
    files = {p.cutout_url: path for p, path in zip(products, paths)}
    resolver = Mock()
    resolver.get.side_effect = lookup.get
    downloader = Mock()
    downloader.download.side_effect = lambda url, dest, cap: shutil.copyfile(files[url], dest)
    storage = store or Store()
    return ProcessTimelapse(resolver, downloader, storage, FitsSequenceRenderer()), storage, downloader


def test_single_realistic_fixture_and_persisted_manifest(tmp_path):
    path = make_fits(tmp_path / "one.fits")
    processor, store, _ = pipeline([path], [product()])
    manifest = processor.execute(request())
    assert manifest["job_id"] == "test-job" and manifest["status"] == "completed"
    frame = manifest["frames"][0]
    assert set(frame) == {"index", "product_id", "timestamp", "object_key"}
    assert frame["index"] == 0 and frame["timestamp"] == "2025-05-01T00:00:00.000Z"
    assert frame["object_key"] == request().prefix + "/frame-000.png"
    assert store.types[frame["object_key"]] == "image/png"
    assert store.calls[-1].endswith("manifest.json")
    assert json.loads(store.objects[store.calls[-1]]) == manifest
    with Image.open(io.BytesIO(store.objects[frame["object_key"]])) as im:
        assert im.format == "PNG" and im.size == (512, 560)
    assert manifest["provenance"]["observations"][0]["source_sha256"]
    assert manifest["provenance"]["distinct_acquisition_times"] == 1


def test_multiple_frames_chronological_indices_and_determinism(tmp_path):
    paths = [make_fits(tmp_path/"late.fits", "2025-05-03T00:00:00", "late"),
             make_fits(tmp_path/"early.fits", "2025-05-01T00:00:00", "early", angle=35)]
    products = [product("a"*64), product("b"*64)]
    processor, store, _ = pipeline(paths, products)
    result = processor.execute(request(tuple(p.product_id for p in products)))
    assert [f["product_id"] for f in result["frames"]] == ["b"*64, "a"*64]
    assert [f["index"] for f in result["frames"]] == [0, 1]
    again = processor.execute(request(tuple(reversed([p.product_id for p in products]))))
    assert again == result
    assert len({Image.open(io.BytesIO(store.objects[f["object_key"]])).size for f in result["frames"]}) == 1


def test_alignment_rotated_wcs_and_shared_intensity(tmp_path):
    from services.image_processing.infrastructure.timelapse import read_frame
    a = make_fits(tmp_path/"a.fits")
    b = make_fits(tmp_path/"b.fits", angle=40)
    grid, shape = target_grid(request())
    first = read_frame(product(), a, request(), grid, shape)
    second = read_frame(product(), b, request(), grid, shape)
    peaks = [np.unravel_index(np.nanargmax(f["image"]), shape) for f in [first, second]]
    assert np.linalg.norm(np.subtract(*peaks)) <= 1
    assert np.nanmean(np.abs(first["image"] - second["image"])) < 0.02
    # Same physical intensity always maps to the same RGB value with shared limits.
    np.testing.assert_array_equal(intensity_pixels(np.array([[2.]]), 0, 10),
                                  intensity_pixels(np.array([[2., 3.]]), 0, 10)[:, :1])


def test_nonfinite_pixels_remain_missing_and_visible(tmp_path):
    data = np.ones((79,79)); data[30:35,30:35] = np.nan; data[40,40] = np.inf
    path = make_fits(tmp_path/"nan.fits", data=data)
    processor, _store, _ = pipeline([path], [product()])
    manifest = processor.execute(request())
    assert 0 < manifest["provenance"]["observations"][0]["coverage_fraction"] < 1
    np.testing.assert_array_equal(intensity_pixels(np.array([[np.nan]]), 0, 1), [[[55,70,85]]])


@pytest.mark.parametrize("mutation", ["missing-image", "cube", "oversized", "outside", "no-time", "bad-scale", "bad-unit", "detector", "inconsistent-time", "variance-shape"])
def test_invalid_fits_is_rejected_before_upload(tmp_path, mutation):
    path = make_fits(tmp_path/"bad.fits")
    with fits.open(path,mode="update") as hdus:
        if mutation == "missing-image": del hdus["IMAGE"]
        elif mutation == "cube": hdus["IMAGE"].data = np.ones((2,3,3))
        elif mutation == "oversized": hdus["IMAGE"].data = np.ones((513,3))
        elif mutation == "outside": hdus["IMAGE"].header["CRVAL1"] = 0
        elif mutation == "no-time":
            del hdus["IMAGE"].header["DATE-OBS"]; del hdus["IMAGE"].header["MJD-OBS"]
            hdus["IMAGE"].header["DATE"] = "2025-01-01"
        elif mutation == "bad-scale": hdus["IMAGE"].header["TIMESYS"] = "bogus"
        elif mutation == "bad-unit": hdus["IMAGE"].header["BUNIT"] = "adu"
        elif mutation == "detector": hdus["IMAGE"].header["DETECTOR"] = 3
        elif mutation == "inconsistent-time": hdus["IMAGE"].header["MJD-OBS"] = 60000
        elif mutation == "variance-shape": hdus["VARIANCE"].data = np.ones((2,2))
    processor, _store, _ = pipeline([path], [product()])
    with pytest.raises((ValueError, OSError)):
        processor.execute(request())
    assert _store.calls == []


def test_invalid_bytes_and_all_nan(tmp_path):
    path = tmp_path/"not.fits"; path.write_bytes(b"not fits" * 500)
    processor, _store, _ = pipeline([path], [product()])
    with pytest.raises(OSError): processor.execute(request())
    path = make_fits(tmp_path/"empty.fits",data=np.full((79,79),np.nan))
    processor, _, _ = pipeline([path],[product()])
    with pytest.raises(ValueError,match="No valid coverage"): processor.execute(request())


def test_duplicate_product_and_acquisition_policy(tmp_path):
    paths=[make_fits(tmp_path/"a.fits"),make_fits(tmp_path/"b.fits")]
    products=[product("a"*64),product("b"*64)]
    processor, _, downloader = pipeline(paths,products)
    result=processor.execute(request(("a"*64,"b"*64,"a"*64)))
    assert downloader.download.call_count == 2
    assert len(result["frames"]) == 1
    assert len(result["provenance"]["discarded_duplicates"]) == 1


def test_same_time_distinct_observations_are_deterministic(tmp_path):
    paths=[make_fits(tmp_path/"a.fits",obsid="other"),make_fits(tmp_path/"b.fits")]
    processor, _, _=pipeline(paths,[product("b"*64),product("a"*64)])
    result=processor.execute(request(("b"*64,"a"*64)))
    assert [f["product_id"] for f in result["frames"]] == ["a"*64,"b"*64]
    assert result["provenance"]["distinct_acquisition_times"] == 1


def test_tai_to_utc_and_mjd_only():
    t,_=acquisition_time({"DATE-OBS":"2025-05-01T00:00:37","TIMESYS":"TAI"})
    assert t.isot == "2025-05-01T00:00:00.000"
    t,meta=acquisition_time({"MJD-OBS":60796,"TIMESYS":"UTC"})
    assert t.isot.startswith("2025-05-01") and meta["time_keyword"] == "MJD-OBS"


@pytest.mark.parametrize("fail_at", [1,2,3])
def test_upload_failures_never_return_completed_manifest(tmp_path,fail_at):
    path=make_fits(tmp_path/"one.fits")
    processor, store, _=pipeline([path],[product()],Store(fail_at))
    with pytest.raises(OSError,match="upload failure"): processor.execute(request())
    assert not any(k.endswith("manifest.json") for k in store.objects)


@pytest.mark.parametrize("change", [
    {"cutout_url":"https://evil.example/fits"}, {"detector":"SPHEREx-D3"},
    {"ra_deg":1}, {"product_id":"b"*64}, {"time_bounds_lower":float('nan')},
])
def test_reference_security_before_download(change):
    resolver=Mock(); resolver.get.return_value=product(**change)
    downloader=Mock()
    processor=ProcessTimelapse(resolver,downloader,Store(),FitsSequenceRenderer())
    with pytest.raises(ValueError): processor.execute(request())
    downloader.download.assert_not_called()


def test_missing_product_and_network_failure(tmp_path):
    resolver=Mock(); resolver.get.return_value=None
    downloader=Mock()
    with pytest.raises(ValueError,match="not found"):
        ProcessTimelapse(resolver,downloader,Store(),FitsSequenceRenderer()).execute(request())
    resolver.get.side_effect=TimeoutError("Observation unavailable")
    with pytest.raises(TimeoutError):
        ProcessTimelapse(resolver,downloader,Store(),FitsSequenceRenderer()).execute(request())
    downloader.download.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"job_id":"../escape"}, {"job_id":"a/b"}, {"product_ids":[]}, {"product_ids":["a"*64]*11},
    {"product_ids":["not-an-id"]}, {"size_deg":0.3}, {"size_deg":0},
    {"ra_deg":360}, {"dec_deg":91}, {"ra_deg":float('nan')}, {"detector":"D2"},
])
def test_request_limits(changes):
    values={"job_id":"job","product_ids":["a"*64],"ra_deg":210.80227,"dec_deg":54.34895,"size_deg":.1}
    with pytest.raises(ValueError): TimelapseRequest(**dict(values,**changes))


def test_s3_content_types_configuration_and_keys(tmp_path,monkeypatch):
    monkeypatch.setenv("S3_BUCKET","fixture-bucket")
    monkeypatch.setenv("S3_ENDPOINT_URL","http://127.0.0.1:9000")
    monkeypatch.setenv("S3_ACCESS_KEY","fixture-key")
    monkeypatch.setenv("S3_SECRET_KEY","fixture-secret")
    with patch("services.image_processing.infrastructure.storage.boto3.client") as factory:
        store=S3ScientificObjectStore()
    path=tmp_path/"image.png"; path.write_bytes(b"fixture")
    ref=ImageReference("timelapses/job/frame-000.png","image/png")
    store.upload_file(path,ref)
    factory.return_value.upload_file.assert_called_once_with(str(path),"fixture-bucket",ref.object_key,ExtraArgs={"ContentType":"image/png"})
    assert factory.call_args.kwargs["endpoint_url"] == "http://127.0.0.1:9000"
    assert factory.call_args.kwargs["config"].read_timeout == 30


def test_celery_adapter_returns_shared_manifest_without_orchestrating():
    from services.image_processing.infrastructure.tasks import render_timelapse
    expected={"job_id":"job","status":"completed","frames":[]}
    with patch("services.image_processing.application.timelapse.ProcessTimelapse") as processor, \
         patch("services.image_processing.infrastructure.tasks.S3ScientificObjectStore"), \
         patch("services.image_processing.infrastructure.tasks.ObservationApiResolver"):
        processor.return_value.execute.return_value=expected
        assert render_timelapse.run("job",["a"*64],210.80227,54.34895,.1) == expected
    assert render_timelapse.name == "eon.image_processing.render_timelapse"


@pytest.mark.parametrize("value", ["invalid-time", "2025-05-01", "2025-99-99T00:00:00"])
def test_invalid_or_incomplete_acquisition_time(value):
    with pytest.raises(ValueError):
        acquisition_time({"DATE-OBS": value, "TIMESYS": "UTC"})
