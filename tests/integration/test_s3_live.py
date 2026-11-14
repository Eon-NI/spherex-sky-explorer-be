"""Opt-in MinIO integration; requires configured bucket and EON_RUN_S3=1."""
import os
from pathlib import Path
import uuid
import pytest
from services.image_processing.domain.models import ImageReference
from services.image_processing.infrastructure.storage import S3ScientificObjectStore

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(os.getenv("EON_RUN_S3") != "1", reason="S3 integration opt-in")]


def test_upload_and_download_scientific_file(tmp_path: Path):
    store = S3ScientificObjectStore()
    reference = ImageReference(f"integration-tests/{uuid.uuid4()}.fits")
    source = tmp_path / "source.fits"
    target = tmp_path / "target.fits"
    source.write_bytes(b"SIMPLE  =                    T" + b" " * 2850)
    try:
        assert not store.exists(reference)
        store.upload_file(source, reference)
        assert store.exists(reference)
        store.download_file(reference, target)
        assert target.read_bytes() == source.read_bytes()
    finally:
        store.client.delete_object(Bucket=store.bucket, Key=reference.object_key)
