"""Opt-in full stack test: EON_RUN_STACK=1 requires Compose and migrated databases."""
import json
import os
import time
from urllib.request import Request, urlopen
import pytest

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(os.getenv("EON_RUN_STACK") != "1", reason="Compose stack opt-in")]


def _json(url: str, body: dict | None = None):
    request = Request(url, data=None if body is None else json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"} if body is not None else {})
    with urlopen(request, timeout=90) as response:
        return response.status, json.load(response)


def test_temporal_cutout_job_and_metadata():
    observation = os.getenv("EON_TEST_OBSERVATION_URL", "http://localhost:8001")
    image = os.getenv("EON_TEST_IMAGE_URL", "http://localhost:8002")
    status, found = _json(observation + "/api/v1/observations/temporal/"
                          "?ra_deg=210.80227&dec_deg=54.34895&size_deg=0.1"
                          "&detector=SPHEREx-D2&limit=10")
    assert status == 200 and found["results"]
    item = found["results"][0]
    status, submitted = _json(image + "/api/v1/cutouts/", {
        "product_id": item["product_id"], "ra_deg": 210.80227,
        "dec_deg": 54.34895, "size_deg": 0.1,
    })
    assert status == 202
    for _ in range(60):
        _, job = _json(image + submitted["status_url"])
        if job["state"] in {"succeeded", "failed"}:
            break
        time.sleep(5)
    assert job["state"] == "succeeded", job
    cutout_id = job["result"]["cutout_id"]
    _, metadata = _json(image + f"/api/v1/cutouts/{cutout_id}/metadata/")
    assert metadata["fits"]["flags_available"]
    assert metadata["fits"]["wavelength_um"] > 0
    with urlopen(image + f"/api/v1/cutouts/{cutout_id}/fits/", timeout=30) as response:
        assert response.headers["Content-Type"].startswith("application/fits")
        assert response.read(30).startswith(b"SIMPLE  =")
