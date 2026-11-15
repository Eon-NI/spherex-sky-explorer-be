"""Explicit bounded SIA2 point fallback; identical temporal reference DTO, not TAP data."""
import logging
import os
from time import sleep
from urllib.parse import urlencode, urlsplit
from xml.etree.ElementTree import ParseError

from requests.exceptions import ConnectionError, RequestException, Timeout

from services.observation.domain.models import COLLECTIONS, TemporalObservation, TemporalSearch
from services.observation.infrastructure.irsa import _get_collection, _number, _text
from services.observation.infrastructure.tap import (
    IrsaTapCatalog,
    TapSession,
    TapUnavailable,
    validate_reference,
)

logger = logging.getLogger(__name__)


class IrsaSiaTemporalCatalog:
    def search(self, query: TemporalSearch) -> list[TemporalObservation]:
        products = {}
        # Reuse SIA's 32 MiB response cap, QUERY_STATUS and MJD-day validation.
        # radius=0 is point containment, NOT a finite-area overlap approximation.
        for collection in COLLECTIONS:
            for attempt in range(2):
                try:
                    with TapSession(min(30, max(1, int(os.getenv("IRSA_TIMEOUT_SECONDS", "60"))))) as session:
                        rows = _get_collection(collection, query, session=session, radius_deg=0)
                    break
                except (RequestException, ValueError, ParseError) as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    transient = isinstance(exc, (ConnectionError, Timeout)) or status in {502, 503, 504}
                    if attempt == 0 and transient:
                        logger.warning("Retrying transient SIA2 point discovery failure",
                                       extra={"collection": collection, "http_status": status})
                        sleep(0.5)
                        continue
                    logger.warning("SIA2 temporal point discovery failed", extra={
                        "discovery_source": "sia2", "collection": collection,
                        "exception_type": type(exc).__name__, "http_status": session.last_status,
                        "transport_error_type": session.transport_error,
                    })
                    raise
            required = {"access_url", "energy_bandpassname", "t_min", "t_max", "obs_collection"}
            if not required <= set(rows.colnames):
                raise ValueError("SIA temporal fields missing")
            for row in rows:
                if _text(row, "energy_bandpassname") != query.detector:
                    continue
                if _text(row, "obs_collection") != collection:
                    raise ValueError("Unexpected SIA collection")
                start, stop = _number(row, "t_min"), _number(row, "t_max")
                if start is None or stop is None or stop < start:
                    raise ValueError("Invalid SIA acquisition interval")
                source = _text(row, "access_url") or ""
                parts = urlsplit(source)
                if parts.query or parts.fragment:
                    raise ValueError("SIA source must be an original artifact URL")
                artifact = parts.path.lstrip("/")
                cutout = source + "?" + urlencode({"center": f"{query.ra_deg:.12g},{query.dec_deg:.12g}d",
                                                   "size": f"{query.size_deg:.12g}"})
                validate_reference(cutout, artifact, query)
                pid = TemporalObservation.product_key(artifact, query.ra_deg, query.dec_deg,
                                                     query.size_deg, query.detector, start)
                products.setdefault(pid, TemporalObservation(pid, artifact, cutout, start,
                                    query.ra_deg, query.dec_deg, query.size_deg, query.detector))
        ordered = sorted(products.values(), key=lambda p: (p.time_bounds_lower, p.artifact_uri, p.product_id))
        logger.info("SIA2 temporal point discovery completed", extra={"discovery_source": "sia2",
                    "time_format": "MJD", "time_unit": "d", "time_scale": "UTC",
                    "available_count": len(ordered), "result_count": min(len(ordered), query.limit)})
        return ordered[:query.limit]


class IrsaTemporalCatalog:
    def __init__(self, primary=None, fallback=None):
        # Native TAP remains independently usable with its configured timeout.
        # The HTTP path uses a 10-second TAP socket-read timeout before fallback.
        self.primary = primary if primary is not None else IrsaTapCatalog(read_timeout=10)
        self.fallback = fallback if fallback is not None else IrsaSiaTemporalCatalog()

    def search(self, query: TemporalSearch) -> list[TemporalObservation]:
        try:
            return self.primary.search(query)
        except TapUnavailable:
            logger.warning("TAP transport unavailable; using SIA2 point temporal discovery",
                           extra={"discovery_source": "sia2", "tap_fallback": True})
            return self.fallback.search(query)
