"""SIA2 adapter following the official SPHEREx astroquery notebook."""
import os
import re
from urllib.parse import urlsplit
from astropy import units as u
from astropy.coordinates import SkyCoord
from astroquery.ipac.irsa import Irsa, conf
import numpy as np
from services.observation.domain.models import Observation, SkySearch

OPTIONAL = ("obs_id", "access_format", "s_region", "energy_bandpassname", "t_min", "t_max", "em_min", "em_max", "s_ra", "s_dec")


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


class IrsaSiaCatalog:
    def search(self, query: SkySearch) -> list[Observation]:
        conf.timeout = int(os.getenv("IRSA_TIMEOUT_SECONDS", "60"))
        coordinate = SkyCoord(ra=query.ra_deg * u.deg, dec=query.dec_deg * u.deg, frame="icrs")
        rows = Irsa.query_sia(pos=(coordinate, query.radius_deg * u.deg), collection=list(query.collections))
        result = []
        for row in rows:
            result.append(Observation(
                publisher_did=_text(row, "obs_publisher_did") or "",
                collection=_text(row, "obs_collection") or "",
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


def _processing_version(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"/level2/[^/]+/([^/]+)/[1-6]/[^/]+\.fits$", urlsplit(url).path)
    return match.group(1) if match else None
