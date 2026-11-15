"""Jobs HTTP client. Spectral never reads Jobs Redis keys directly."""
import json
import os
from urllib.request import Request, urlopen


class JobsClient:
    def __init__(self):
        self.base_url = os.environ.get("JOB_SERVICE_URL", "http://jobs:8000").rstrip("/")

    def submit(self, task: str, payload: dict, idempotency_key: str) -> str:
        request = Request(f"{self.base_url}/api/v1/jobs/",
                          data=json.dumps({"task": task, "idempotency_key": idempotency_key,
                                           "payload": payload}).encode(),
                          headers={"Content-Type": "application/json",
                                   "X-EON-Internal-Token": os.environ["EON_INTERNAL_TOKEN"]})
        with urlopen(request, timeout=15) as response:
            return json.load(response)["id"]

    def get(self, job_id: str) -> dict:
        with urlopen(Request(f"{self.base_url}/api/v1/jobs/{job_id}/"), timeout=15) as response:
            return json.load(response)
