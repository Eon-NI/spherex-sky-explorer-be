"""Processing only: no jobs database, state machine, broker submission or FITS JSON."""
import json
import tempfile
from math import isfinite
from pathlib import Path
from typing import Protocol

from services.image_processing.application.ports import (
    CutoutDownloader,
    ProductResolver,
    ScientificObjectStore,
)
from services.image_processing.domain.models import ImageReference, SourceProduct
from services.image_processing.domain.security import validate_cutout_url
from services.image_processing.domain.timelapse import TimelapseRequest


class SequenceRenderer(Protocol):
    def render(self, inputs: list[tuple[SourceProduct, Path]], request: TimelapseRequest,
               directory: Path) -> tuple[list[dict], dict]: ...


class ProcessTimelapse:
    def __init__(self, resolver: ProductResolver, downloader: CutoutDownloader,
                 storage: ScientificObjectStore, renderer: SequenceRenderer,
                 max_bytes: int = 8 * 1024 * 1024):
        if not 2880 <= max_bytes <= 16 * 1024 * 1024:
            raise ValueError("Per-input limit must be 2880 bytes to 16 MiB")
        self.resolver, self.downloader = resolver, downloader
        self.storage, self.renderer, self.max_bytes = storage, renderer, max_bytes

    def execute(self, request: TimelapseRequest) -> dict:
        # Resolve and authorize ALL inputs before downloading anything.
        products = []
        for pid in request.product_ids:
            product = self.resolver.get(pid)
            if product is None:
                raise ValueError(f"Observation product not found: {pid}")
            if product.product_id != pid or product.detector != request.detector:
                raise ValueError("Resolved product identity or detector mismatch")
            if (product.ra_deg, product.dec_deg, product.size_deg) != (request.ra_deg, request.dec_deg, request.size_deg):
                raise ValueError("Region differs from Observation's verified TAP query")
            if not isfinite(product.time_bounds_lower):
                raise ValueError("Non-finite Observation time bound")
            validate_cutout_url(product.cutout_url, product.artifact_uri,
                                request.ra_deg, request.dec_deg, request.size_deg)
            products.append(product)
        with tempfile.TemporaryDirectory(prefix="eon-timelapse-") as folder:
            directory = Path(folder)
            inputs = []
            for product in products:
                path = directory / f"{product.product_id}.fits"
                self.downloader.download(product.cutout_url, path, self.max_bytes)
                if not path.exists() or not 2880 <= path.stat().st_size <= self.max_bytes:
                    raise ValueError("Downloaded FITS exceeds bounds or is empty")
                inputs.append((product, path))
            frames, provenance = self.renderer.render(inputs, request, directory)
            if not frames:
                raise ValueError("No valid frames were generated")
            for frame in frames:
                pid, index = frame["product_id"], frame["index"]
                fits_path = directory / f"{pid}.fits"
                self.storage.upload_file(fits_path, ImageReference(f"{request.prefix}/source-{pid}.fits"))
                self.storage.upload_file(directory / f"frame-{index:03d}.png",
                                         ImageReference(frame["object_key"], "image/png"))
            # Persist the manifest LAST. A failure raises; no completed result is returned.
            manifest = {"job_id": request.job_id, "status": "completed", "frames": frames,
                        "provenance": provenance}
            manifest_path = directory / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, allow_nan=False), encoding="utf-8")
            self.storage.upload_file(manifest_path,
                                     ImageReference(f"{request.prefix}/manifest.json", "application/json"))
            return manifest
