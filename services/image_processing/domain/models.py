"""Scientific image processing request types; no processing is claimed yet."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImageReference:
    object_key: str
    content_type: str = "application/fits"


@dataclass(frozen=True, slots=True)
class CutoutRequest:
    image: ImageReference
    ra_deg: float
    dec_deg: float
    size_deg: float
