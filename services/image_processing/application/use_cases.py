from pathlib import Path
import tempfile
from services.image_processing.application.ports import (
    CutoutDownloader, CutoutRepository, FitsReader, JobSubmitter, ProductResolver,
    ScientificObjectStore,
)
from services.image_processing.domain.models import CutoutRequest, FitsMetadata, ImageReference
from services.image_processing.domain.security import validate_cutout_url


class RequestFitsCutout:
    def __init__(self, submitter: JobSubmitter):
        self.submitter = submitter

    def execute(self, request: CutoutRequest) -> str:
        return self.submitter.submit_cutout(request)


class CreateFitsCutout:
    def __init__(self, resolver: ProductResolver, downloader: CutoutDownloader,
                 storage: ScientificObjectStore, reader: FitsReader, repository: CutoutRepository,
                 max_bytes: int):
        self.resolver, self.downloader = resolver, downloader
        self.storage, self.reader, self.repository = storage, reader, repository
        self.max_bytes = max_bytes

    def execute(self, request: CutoutRequest) -> ImageReference:
        product = self.resolver.get(request.product_id)
        if product is None:
            raise ValueError("Unknown product_id")
        # The TAP CONTAINS predicate certified this exact coordinate for this artifact.
        if (product.ra_deg, product.dec_deg, product.size_deg) != (
            request.ra_deg, request.dec_deg, request.size_deg
        ):
            raise ValueError("Cutout coordinates differ from verified TAP query")
        url = validate_cutout_url(product.cutout_url, product.artifact_uri,
                                  request.ra_deg, request.dec_deg, request.size_deg)
        reference = ImageReference(f"cutouts/{request.idempotency_key}.fits")
        cached = self.repository.get(request.idempotency_key)
        if cached is not None and self.storage.exists(cached):
            return cached
        if self.storage.exists(reference):
            with tempfile.TemporaryDirectory(prefix="eon-cache-") as temp_dir:
                path = Path(temp_dir) / "cutout.fits"
                self.storage.download_file(reference, path)
                metadata = self.reader.inspect(path, request.ra_deg, request.dec_deg)
                self.repository.save(request, product, reference, metadata)
            return reference
        with tempfile.TemporaryDirectory(prefix="eon-cutout-") as temp_dir:
            path = Path(temp_dir) / "cutout.fits"
            self.downloader.download(url, path, self.max_bytes)
            metadata = self.reader.inspect(path, request.ra_deg, request.dec_deg)
            self.storage.upload_file(path, reference)
            # Persist compact provenance only after successful upload.
            self.repository.save(request, product, reference, metadata)
        return reference


class InspectFitsMetadata:
    def __init__(self, storage: ScientificObjectStore, reader: FitsReader,
                 repository: CutoutRepository):
        self.storage, self.reader, self.repository = storage, reader, repository

    def execute(self, key: str, ra_deg: float | None = None, dec_deg: float | None = None) -> FitsMetadata | None:
        reference = self.repository.get(key)
        if reference is None:
            return None
        with tempfile.TemporaryDirectory(prefix="eon-inspect-") as temp_dir:
            path = Path(temp_dir) / "cutout.fits"
            self.storage.download_file(reference, path)
            return self.reader.inspect(path, ra_deg, dec_deg)
