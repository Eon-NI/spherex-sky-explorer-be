"""Internal service API adapters; no cross-service database access."""
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from services.image_processing.domain.models import CutoutRequest, SourceProduct


class ObservationApiResolver:
    def __init__(self) -> None:
        self.base_url = os.environ.get("OBSERVATION_SERVICE_URL", "http://observation:8000").rstrip("/")

    def get(self, product_id: str) -> SourceProduct | None:
        request = Request(f"{self.base_url}/api/v1/observations/temporal/{product_id}/")
        try:
            with urlopen(request, timeout=15) as response:
                data = json.load(response)
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        return SourceProduct(**data)


class JobApiSubmitter:
    def __init__(self) -> None:
        self.base_url = os.environ.get("JOB_SERVICE_URL", "http://jobs:8000").rstrip("/")

    def submit_cutout(self, request: CutoutRequest) -> str:
        body = json.dumps({"task": "eon.image_processing.create_cutout",
                           "idempotency_key": request.idempotency_key,
                           "payload": {"product_id": request.product_id, "ra_deg": request.ra_deg,
                                       "dec_deg": request.dec_deg, "size_deg": request.size_deg}}).encode()
        url = f"{self.base_url}/api/v1/jobs/"
        outbound = Request(url, data=body, headers={"Content-Type": "application/json",
                                                     "X-EON-Internal-Token": os.environ["EON_INTERNAL_TOKEN"]})
        with urlopen(outbound, timeout=15) as response:
            return json.load(response)["id"]


class JobApiReader:
    def __init__(self) -> None:
        self.base_url = os.environ.get("JOB_SERVICE_URL", "http://jobs:8000").rstrip("/")

    def get(self, job_id: str) -> dict:
        request = Request(f"{self.base_url}/api/v1/jobs/{job_id}/")
        with urlopen(request, timeout=15) as response:
            return json.load(response)
