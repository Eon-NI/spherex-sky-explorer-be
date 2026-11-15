"""Framework-free request and manifest contracts for the BE-03 boundary."""
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite


@dataclass(frozen=True, slots=True)
class TimelapseRequest:
    job_id: str
    product_ids: tuple[str, ...]
    ra_deg: float
    dec_deg: float
    size_deg: float
    detector: str = "SPHEREx-D2"

    def __post_init__(self):
        if not isinstance(self.job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", self.job_id) or ".." in self.job_id:
            raise ValueError("job_id must be an opaque safe identifier (1-128 characters)")
        if not isinstance(self.product_ids, (tuple, list)) or not 1 <= len(self.product_ids) <= 10:
            raise ValueError("Provide 1-10 temporal product identifiers")
        if any(not isinstance(pid, str) or not re.fullmatch(r"[0-9a-f]{64}", pid) for pid in self.product_ids):
            raise ValueError("Invalid Observation product identifier")
        values = (self.ra_deg, self.dec_deg, self.size_deg)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in values):
            raise ValueError("Coordinates and size must be finite numbers")
        if not 0 <= self.ra_deg < 360 or not -90 <= self.dec_deg <= 90:
            raise ValueError("Coordinates outside ICRS range")
        if not 0 < self.size_deg <= 0.2:
            raise ValueError("MVP square size_deg must be in (0, 0.2]")
        if self.detector not in tuple(f"SPHEREx-D{i}" for i in range(1, 7)):
            raise ValueError("Unsupported detector")
        for name in ("ra_deg", "dec_deg", "size_deg"):
            object.__setattr__(self, name, float(getattr(self, name)))
        object.__setattr__(self, "product_ids", tuple(sorted(set(self.product_ids))))

    @property
    def fingerprint(self):
        payload = json.dumps({"products": self.product_ids, "ra": self.ra_deg,
                              "dec": self.dec_deg, "size": self.size_deg,
                              "detector": self.detector, "pipeline": "be02-v1"}, sort_keys=True)
        return sha256(payload.encode()).hexdigest()[:24]

    @property
    def prefix(self):
        return f"timelapses/{self.job_id}/{self.fingerprint}"
