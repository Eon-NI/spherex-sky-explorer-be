"""IRSA SIA adapter using the documented astroquery API."""
from astropy import units as u
from astropy.coordinates import SkyCoord
from astroquery.ipac.irsa import Irsa
from services.observation.domain.models import Observation, SkySearch

COLLECTIONS = ("spherex_dr1", "spherex_qr3")


class IrsaSiaCatalog:
    def search(self, query: SkySearch) -> list[Observation]:
        coordinates = SkyCoord(query.ra_deg, query.dec_deg, unit="deg", frame="icrs")
        rows = Irsa.query_sia(
            pos=(coordinates, query.radius_deg * u.deg), collection=list(COLLECTIONS)
        )
        return [
            Observation(
                publisher_did=str(row["obs_publisher_did"]),
                collection=str(row["obs_collection"]),
                access_url=str(row["access_url"]),
                ra_deg=_optional_float(row["s_ra"]),
                dec_deg=_optional_float(row["s_dec"]),
            )
            for row in rows
        ]


def _optional_float(value: object) -> float | None:
    import numpy as np
    return None if np.ma.is_masked(value) else float(value)
