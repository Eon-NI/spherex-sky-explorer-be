"""IRSA TAP adapter: documented artifact/plane query, with validated ADQL literals."""
import logging
import os
from math import isfinite
from time import perf_counter
from urllib.parse import urlsplit
from astroquery.ipac.irsa import Irsa, conf
from services.observation.domain.models import TemporalObservation, TemporalSearch

logger = logging.getLogger(__name__)


class IrsaTapCatalog:
    @staticmethod
    def build_adql(query: TemporalSearch) -> str:
        # TemporalSearch validates every interpolated numeric and detector value.
        ra, dec, size = (format(value, ".12g") for value in (query.ra_deg, query.dec_deg, query.size_deg))
        return (
            f"SELECT TOP {query.limit} "
            f"'https://irsa.ipac.caltech.edu/' || a.uri || '?center={ra},{dec}d&size={size}' AS uri, "
            "a.uri AS artifact_uri, p.time_bounds_lower "
            "FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid "
            f"WHERE 1 = CONTAINS(POINT('ICRS', {ra}, {dec}), p.poly) "
            f"AND p.energy_bandpassname = '{query.detector}' "
            "ORDER BY p.time_bounds_lower"
        )

    def search(self, query: TemporalSearch) -> list[TemporalObservation]:
        start = perf_counter()
        conf.timeout = int(os.getenv("IRSA_TIMEOUT_SECONDS", "60"))
        try:
            response = Irsa.query_tap(self.build_adql(query), maxrec=query.limit)
            rows = response.to_table() if hasattr(response, "to_table") else response
            results = []
            for row in rows:
                artifact_uri = str(row["artifact_uri"])
                url = str(row["uri"])
                timestamp = float(row["time_bounds_lower"])
                if not isfinite(timestamp) or urlsplit(url).hostname != "irsa.ipac.caltech.edu":
                    raise ValueError("Invalid IRSA TAP response")
                results.append(TemporalObservation(
                    product_id=TemporalObservation.product_key(artifact_uri, query.ra_deg, query.dec_deg,
                                                             query.size_deg, query.detector, timestamp),
                    artifact_uri=artifact_uri, cutout_url=url,
                    time_bounds_lower=timestamp,
                    ra_deg=query.ra_deg, dec_deg=query.dec_deg,
                    size_deg=query.size_deg, detector=query.detector,
                ))
            results.sort(key=lambda item: item.time_bounds_lower)
            unique = list({item.product_id: item for item in results}.values())
            return unique[:query.limit]
        except Exception as exc:
            logger.exception("IRSA TAP query failed")
            raise RuntimeError("IRSA TAP query unavailable") from exc
        finally:
            logger.info("IRSA TAP query completed", extra={"duration_seconds": perf_counter() - start,
                                                         "result_count": len(locals().get("results", []))})
