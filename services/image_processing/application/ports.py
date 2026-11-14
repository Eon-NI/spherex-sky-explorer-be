from pathlib import Path
from typing import Protocol
from services.image_processing.domain.models import (
    CutoutRequest, FitsMetadata, ImageReference, SourceProduct,
)


class ScientificObjectStore(Protocol):
    def exists(self, reference: ImageReference) -> bool: ...
    def upload_file(self, path: Path, reference: ImageReference) -> None: ...
    def download_file(self, reference: ImageReference, path: Path) -> None: ...


class ProductResolver(Protocol):
    def get(self, product_id: str) -> SourceProduct | None: ...


class CutoutDownloader(Protocol):
    def download(self, url: str, destination: Path, max_bytes: int) -> int: ...


class FitsReader(Protocol):
    def inspect(self, path: Path, ra_deg: float | None = None, dec_deg: float | None = None) -> FitsMetadata: ...


class CutoutRepository(Protocol):
    def get(self, key: str) -> ImageReference | None: ...
    def save(self, request: CutoutRequest, product: SourceProduct, reference: ImageReference,
             metadata: FitsMetadata) -> None: ...


class JobSubmitter(Protocol):
    def submit_cutout(self, request: CutoutRequest) -> str: ...
