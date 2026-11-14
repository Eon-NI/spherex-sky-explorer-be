"""Direct IRSA SIA2 adapter; avoids optional VO capabilities discovery."""
from io import BytesIO
import logging
import os
import re
from time import perf_counter
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from astropy import units as u
from astropy.io.votable import parse
import numpy as np
import requests

from services.observation.domain.models import Observation, SkySearch

logger = logging.getLogger(__name__)
SIA_URL = "https://irsa.ipac.caltech.edu/SIA"
OPTIONAL = ("obs_id", "access_format", "s_region", "energy_bandpassname", "t_min", "t_max", "em_min", "em_max", "s_ra", "s_dec")
MAX_VOTABLE_BYTES = 32 * 1024 * 1024


def _value(row: object, name: str) -> object | None:
    try:
        value = row[name]
    except (KeyError, IndexError, ValueError):
        return None
    if value is None or np.ma.is_masked(value):
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _text(row: object, name: str) -> str | None:
    value = _value(row, name)
    return None if value is None else str(value)


def _number(row: object, name: str) -> float | None:
    value = _value(row, name)
    return None if value is None else float(value)


def _read_votable(content: bytes):
    root = ET.fromstring(content)
    statuses = [node.get("value", "").upper() for node in root.iter()
                if node.tag.rsplit("}", 1)[-1] in ("INFO", "PARAM")
                and node.get("name", "").upper() == "QUERY_STATUS"]
    if not statuses or any(value != "OK" for value in statuses):
        raise ValueError(f"Invalid SIA QUERY_STATUS: {statuses}")
    votable = parse(BytesIO(content))
    tables = [table for table in votable.iter_tables()
              if "access_url" in {field.name for field in table.fields}]
    if len(tables) != 1:
        raise ValueError("SIA response must contain one access_url table")
    table = tables[0].to_table(use_names_over_ids=True)
    for name in ("t_min", "t_max"):
        if name in table.colnames and (table[name].unit is None or u.Unit(table[name].unit) != u.day):
            raise ValueError(f"SIA {name} must be in MJD days")
    return table


def _get_collection(collection: str, query: SkySearch):
    timeout = max(1, min(int(os.getenv("IRSA_TIMEOUT_SECONDS", "60")), 120))
    params = {
        "COLLECTION": collection,
        "POS": f"CIRCLE {query.ra_deg:.12g} {query.dec_deg:.12g} {query.radius_deg:.12g}",
        "RESPONSEFORMAT": "VOTABLE",
    }
    # The endpoint and all query keys are fixed; user input has passed SkySearch validation.
    with requests.get(SIA_URL, params=params, timeout=(15, timeout), stream=True,
                      allow_redirects=False) as response:
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("Unexpected IRSA SIA redirect")
        chunks, size = [], 0
        for chunk in response.iter_content(128 * 1024):
            size += len(chunk)
            if size > MAX_VOTABLE_BYTES:
                raise ValueError("IRSA SIA response exceeds size limit")
            chunks.append(chunk)
    return _read_votable(b"".join(chunks))


class IrsaSiaCatalog:
    def search(self, query: SkySearch) -> list[Observation]:
        start = perf_counter()
        result = []
        try:
            for collection in dict.fromkeys(query.collections):
                rows = _get_collection(collection, query)
                for row in rows:
                    result.append(Observation(
                        publisher_did=_text(row, "obs_publisher_did") or "",
                        collection=_text(row, "obs_collection") or collection,
                        access_url=_text(row, "access_url") or "",
                        ra_deg=_number(row, "s_ra"), dec_deg=_number(row, "s_dec"),
                        obs_id=_text(row, "obs_id"), access_format=_text(row, "access_format"),
                        s_region=_text(row, "s_region"), energy_bandpassname=_text(row, "energy_bandpassname"),
                        t_min_mjd=_number(row, "t_min"), t_max_mjd=_number(row, "t_max"),
                        em_min_m=_number(row, "em_min"), em_max_m=_number(row, "em_max"),
                        missing_fields=tuple(name for name in OPTIONAL if _value(row, name) is None),
                        processing_version=_processing_version(_text(row, "access_url")),
                    ))
            priority = {name: index for index, name in enumerate(query.collections)}
            result.sort(key=lambda item: priority.get(item.collection, len(priority)))
            return result
        finally:
            logger.info("IRSA SIA query completed", extra={"duration_seconds": perf_counter() - start,
                                                       "result_count": len(result)})


def _processing_version(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"/level2/[^/]+/([^/]+)/[1-6]/[^/]+\.fits$", urlsplit(url).path)
    return match.group(1) if match else None
