"""Bounded TAP transport, validated ADQL and references for BE-02."""
import logging
import os
from math import isfinite
from time import perf_counter
from urllib.parse import parse_qs, urlsplit

import requests
from astroquery.ipac.irsa import IrsaClass
from pyvo.dal.exceptions import DALQueryError

from services.observation.domain.models import TemporalObservation, TemporalSearch

logger = logging.getLogger(__name__)


class TapSession(requests.Session):
    """Per-query session: PyVO's omitted/None timeout cannot disable the bound."""
    def __init__(self, read_timeout):
        super().__init__()
        self.timeouts = (15, read_timeout)
        # IRSA is public. Inherited proxies/netrc caused proxy 502s in diagnosis.
        # Operators requiring their proxy/CA environment can explicitly opt in.
        self.trust_env = os.getenv("IRSA_TAP_TRUST_ENV", "false").lower() == "true"
        self.last_status = None
        self.transport_error = None

    def request(self, method, url, **kwargs):
        kwargs["timeout"] = self.timeouts
        kwargs["allow_redirects"] = False
        self.last_status = None
        self.transport_error = None
        try:
            response = super().request(method, url, **kwargs)
        except requests.RequestException as exc:
            self.transport_error = type(exc).__name__
            raise
        self.last_status = response.status_code
        return response


def validate_reference(url, artifact_uri, query):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "irsa.ipac.caltech.edu"
            or parts.fragment or not parts.path.startswith((
                "/ibe/data/spherex/dr1/level2/", "/ibe/data/spherex/qr3/level2/"))
            or not parts.path.endswith(".fits") or ".." in parts.path
            or "%" in parts.path or "//" in parts.path
            or parts.path.lstrip("/") != artifact_uri.lstrip("/")):
        raise ValueError("Invalid IRSA TAP reference")
    params = parse_qs(parts.query, strict_parsing=True)
    if set(params) != {"center", "size"} or any(len(v) != 1 for v in params.values()):
        raise ValueError("Invalid IRSA cutout parameters")
    center = params["center"][0]
    if not center.endswith("d"):
        raise ValueError("Missing coordinate unit")
    ra, dec = map(float, center[:-1].split(","))
    size = float(params["size"][0])
    for actual, expected in zip((ra, dec, size), (query.ra_deg, query.dec_deg, query.size_deg)):
        if not isfinite(actual) or abs(actual - expected) > 1e-9:
            raise ValueError("IRSA cutout geometry mismatch")


class TapUnavailable(RuntimeError):
    """Transport/service failure eligible for the documented SIA2 fallback."""


def transport_failure(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (DALQueryError, requests.HTTPError)):
            return False
        if isinstance(exc, (requests.RequestException, OSError)):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class IrsaTapCatalog:
    def __init__(self, read_timeout=None):
        self.read_timeout = read_timeout

    @staticmethod
    def build_adql(query: TemporalSearch) -> str:
        ra, dec, size = (format(value, ".12g") for value in (query.ra_deg, query.dec_deg, query.size_deg))
        return (
            f"SELECT TOP {query.limit} "
            f"'https://irsa.ipac.caltech.edu/' || a.uri || '?center={ra},{dec}d&size={size}' AS uri, "
            "a.uri AS artifact_uri, p.time_bounds_lower "
            "FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid "
            f"WHERE 1 = CONTAINS(POINT('ICRS', {ra}, {dec}), p.poly) "
            f"AND p.energy_bandpassname = '{query.detector}' "
            "ORDER BY p.time_bounds_lower, a.uri"
        )

    def search(self, query: TemporalSearch) -> list[TemporalObservation]:
        start = perf_counter()
        results = []
        try:
            timeout = max(1, min(int(os.getenv("IRSA_TIMEOUT_SECONDS", "60")), 120))
            if self.read_timeout is not None:
                timeout = min(timeout, max(1, self.read_timeout))
            # A fresh client/session avoids mutating Astroquery's global singleton.
            client = IrsaClass()
            with TapSession(timeout) as session:
                client._session = session
                response = client.query_tap(self.build_adql(query), maxrec=query.limit)
            # PyVO raises for ERROR. OVERFLOW may legitimately mean maxrec reached;
            # this endpoint explicitly promises only a bounded chronological prefix.
            rows = response.to_table() if hasattr(response, "to_table") else response
            for row in rows:
                artifact_uri, url = str(row["artifact_uri"]), str(row["uri"])
                timestamp = float(row["time_bounds_lower"])
                if not isfinite(timestamp):
                    raise ValueError("Invalid TAP observation time")
                validate_reference(url, artifact_uri, query)
                results.append(TemporalObservation(
                    product_id=TemporalObservation.product_key(artifact_uri, query.ra_deg, query.dec_deg,
                                                             query.size_deg, query.detector, timestamp),
                    artifact_uri=artifact_uri, cutout_url=url, time_bounds_lower=timestamp,
                    ra_deg=query.ra_deg, dec_deg=query.dec_deg,
                    size_deg=query.size_deg, detector=query.detector,
                ))
            results.sort(key=lambda item: (item.time_bounds_lower, item.artifact_uri, item.product_id))
            unique = list({item.product_id: item for item in results}.values())
            return unique[:query.limit]
        except Exception as exc:
            status = getattr(locals().get("session"), "last_status", None)
            logger.warning("IRSA TAP query failed", extra={
                "upstream_endpoint": "https://irsa.ipac.caltech.edu/TAP/sync",
                "execution_mode": "sync", "exception_type": type(exc).__name__,
                "http_status": status,
                "transport_error_type": getattr(locals().get("session"), "transport_error", None),
            })
            unavailable = not isinstance(exc, DALQueryError) and (transport_failure(exc) or (status is not None and status >= 500))
            error = TapUnavailable if unavailable else RuntimeError
            raise error("IRSA TAP query unavailable") from exc
        finally:
            logger.info("IRSA TAP query completed", extra={"duration_seconds": perf_counter() - start,
                                                          "result_count": len(results)})
