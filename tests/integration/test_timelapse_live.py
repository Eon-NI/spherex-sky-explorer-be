"""Opt-in real BE-01 -> IRSA -> PNG -> configured S3, never fabricated references."""
import json
import os
from pathlib import Path

import pytest
from PIL import Image

from services.image_processing.application.timelapse import ProcessTimelapse
from services.image_processing.domain.models import ImageReference
from services.image_processing.domain.timelapse import TimelapseRequest
from services.image_processing.infrastructure.clients import ObservationApiResolver
from services.image_processing.infrastructure.downloader import IrsaCutoutDownloader
from services.image_processing.infrastructure.storage import S3ScientificObjectStore
from services.image_processing.infrastructure.timelapse import FitsSequenceRenderer

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('EON_RUN_BE02_LIVE')!='1',reason='Real Observation/IRSA/S3 opt-in')]


def test_live_timelapse_and_retrieval(tmp_path: Path):
    payload=json.loads(Path(os.environ['EON_BE02_REQUEST_FILE']).read_text(encoding='utf-8'))
    request=TimelapseRequest(**payload)
    store=S3ScientificObjectStore()
    manifest=ProcessTimelapse(ObservationApiResolver(),IrsaCutoutDownloader(),store,FitsSequenceRenderer()).execute(request)
    assert manifest['status']=='completed' and manifest['frames']
    assert [f['index'] for f in manifest['frames']]==list(range(len(manifest['frames'])))
    for frame in manifest['frames']:
        path=tmp_path/f"frame-{frame['index']}.png"
        store.download_file(ImageReference(frame['object_key'],'image/png'),path)
        with Image.open(path) as image: assert image.format=='PNG' and image.size==(512,560)
        response=store.client.head_object(Bucket=store.bucket,Key=frame['object_key'])
        assert response['ContentType']=='image/png'
    path=tmp_path/'manifest.json'
    store.download_file(ImageReference(request.prefix+'/manifest.json','application/json'),path)
    assert json.loads(path.read_text(encoding='utf-8'))==manifest
    # Intentionally retained for frontend inspection; use a dedicated test job_id/bucket.
