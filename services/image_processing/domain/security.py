"""Strict IRSA URL policy; never follow client-provided or redirected hosts."""
from math import isfinite
from urllib.parse import parse_qs, urlsplit

HOST = "irsa.ipac.caltech.edu"
PREFIXES = ("/ibe/data/spherex/dr1/level2/", "/ibe/data/spherex/qr3/level2/")


def validate_cutout_url(url: str, artifact_uri: str, ra: float, dec: float, size: float) -> str:
    parts = urlsplit(url)
    path = parts.path
    if (parts.scheme != "https" or parts.hostname != HOST or parts.port is not None
            or parts.username is not None or parts.password is not None or parts.fragment
            or not path.startswith(PREFIXES) or not path.lower().endswith(".fits")
            or ".." in path or "%" in path or "//" in path):
        raise ValueError("Untrusted IRSA cutout URL")
    if artifact_uri.lstrip("/") != path.lstrip("/"):
        raise ValueError("Artifact path mismatch")
    query = parse_qs(parts.query, strict_parsing=True)
    if set(query) != {"center", "size"} or any(len(values) != 1 for values in query.values()):
        raise ValueError("Unexpected cutout parameters")
    center = query["center"][0]
    if not center.endswith("d"):
        raise ValueError("Missing angular unit")
    try:
        url_ra, url_dec = (float(value) for value in center[:-1].split(","))
        url_size = float(query["size"][0])
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid cutout parameters") from exc
    if not all(isfinite(value) for value in (url_ra, url_dec, url_size, ra, dec, size)):
        raise ValueError("Cutout parameters must be finite")
    if abs(url_ra - ra) > 1e-9 or abs(url_dec - dec) > 1e-9 or abs(url_size - size) > 1e-9:
        raise ValueError("Cutout query mismatch")
    return url
