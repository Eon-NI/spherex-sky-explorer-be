"""Bounded metadata-only IRSA audit; no FITS downloads or configuration writes."""
import concurrent.futures
import json
from datetime import datetime, timezone
from pathlib import Path
import requests
from services.observation.domain.models import COLLECTIONS, TemporalSearch
from services.observation.infrastructure.irsa import _get_collection
from services.observation.infrastructure.tap import TapSession

REGIONS = [("M101", 210.80227, 54.34895), ("SIA reference", 304.693508808, 42.4436872991), ("ICRS north pole", 0, 90)]

def probe(region):
    name, ra, dec = region
    result = {"region": name, "ra_deg": ra, "dec_deg": dec, "collections": {}}
    query = TemporalSearch(ra, dec, .1, "SPHEREx-D2", 2)
    for collection in COLLECTIONS:
        try:
            with TapSession(10) as session:
                rows = _get_collection(collection, query, session=session, radius_deg=0)
            result["collections"][collection] = {"http_status": 200, "count": len(rows), "detectors": sorted(set(map(str, rows["energy_bandpassname"]))) }
        except (requests.RequestException, ValueError) as exc:
            result["collections"][collection] = {"exception": type(exc).__name__, "http_status": getattr(getattr(exc, "response", None), "status_code", None), "error": str(exc)}
    with requests.Session() as session:
        session.trust_env = False
        try:
            response = session.get("http://127.0.0.1:8001/api/v1/observations/temporal/", params={"ra_deg": ra, "dec_deg": dec, "size_deg": .1, "detector": query.detector, "limit": 2}, timeout=(5, 45))
            result["public_http"] = {"status": response.status_code, "body": response.json()}
        except requests.RequestException as exc:
            result["public_http"] = {"exception": type(exc).__name__, "error": str(exc)}
    return result

if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        regions = list(pool.map(probe, REGIONS))
    evidence = {"checked_at": datetime.now(timezone.utc).isoformat(), "real_multi_region_passed": all(any(c.get("http_status") == 200 and c.get("count", 0) > 0 for c in r["collections"].values()) for r in regions[:2]), "regions": regions}
    target = Path("docs/evidence/temporal_regions/validation.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))
