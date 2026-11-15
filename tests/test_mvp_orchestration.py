"""BE-03 orchestration/delivery guards without real scientific downloads."""
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.test import override_settings
from rest_framework.test import APIRequestFactory

from services.jobs.domain.models import JobState
from services.jobs.infrastructure.celery_jobs import CeleryJobQueue
from services.jobs.infrastructure.timelapses import frame_key
from services.jobs.presentation import timelapses as api

PID = 'a' * 64
JOB = 'c19f23f7-5c6b-4528-b59f-f5ddcbac871e'
PAYLOAD = {'product_ids': [PID], 'ra_deg': 210.8, 'dec_deg': 54.3,
           'size_deg': .1, 'detector': 'SPHEREx-D2'}
RESULT = {'job_id': JOB, 'status': 'completed', 'frames': [
    {'index': 0, 'product_id': PID, 'timestamp': '2025-05-10T17:04:37.741Z',
     'object_key': f'timelapses/{JOB}/' + 'a'*24 + '/frame-000.png'}]}


@pytest.fixture(autouse=True)
def local_cache():
    with override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                           ALLOWED_HOSTS=['testserver']):
        yield


def test_submission_verifies_references_and_enqueues_once(monkeypatch):
    product = {k: PAYLOAD[k] for k in ['ra_deg', 'dec_deg', 'size_deg', 'detector']}
    monkeypatch.setattr(api, 'resolve_product', lambda pid: {'product_id': pid, **product})
    submit = Mock(return_value=JOB)
    monkeypatch.setattr(api, 'CeleryJobQueue', lambda: SimpleNamespace(submit=submit))
    response = api.TimelapseCollectionView.as_view()(APIRequestFactory().post('/', PAYLOAD, format='json'))
    assert response.status_code == 202 and response.data['job_id'] == JOB
    assert submit.call_args.args[0] == api.TASK
    assert 'job_id' not in submit.call_args.args[1]


@pytest.mark.parametrize('product,expected', [(None, 404), ({'product_id': PID}, 400)])
def test_invalid_reference_never_enqueues(monkeypatch, product, expected):
    monkeypatch.setattr(api, 'resolve_product', lambda _: product)
    queue = Mock()
    monkeypatch.setattr(api, 'CeleryJobQueue', queue)
    response = api.TimelapseCollectionView.as_view()(APIRequestFactory().post('/', PAYLOAD, format='json'))
    assert response.status_code == expected
    queue.assert_not_called()


@pytest.mark.parametrize('changes', [{'size_deg': float('nan')}, {'product_ids': ['a'*64]*6},
                                     {'detector': 'All'}, {'size_deg': .3}])
def test_public_resource_bounds(changes):
    serializer = api.TimelapsePayload(data={**PAYLOAD, **changes})
    assert not serializer.is_valid()


def test_server_owned_job_id_passed_to_processor(monkeypatch):
    from services.jobs.infrastructure import celery_jobs
    queue = object.__new__(CeleryJobQueue)
    queue.redis = Mock()
    queue.redis.set.return_value = True
    send = Mock()
    monkeypatch.setattr(celery_jobs.app, 'send_task', send)
    job_id = queue.submit(api.TASK, PAYLOAD, 'b'*64)
    assert send.call_args.kwargs['kwargs']['job_id'] == job_id
    assert send.call_args.kwargs['task_id'] == job_id
    assert send.call_args.kwargs['queue'] == 'image_processing'


def test_frame_prefix_cannot_escape_job():
    assert frame_key(RESULT, JOB, 0).endswith('frame-000.png')
    bad = {**RESULT, 'frames': [{**RESULT['frames'][0], 'object_key': 'private/other.png'}]}
    with pytest.raises(ValueError):
        frame_key(bad, JOB, 0)
    with pytest.raises(IndexError):
        frame_key(RESULT, JOB, 1)


def test_manifest_preserved_delivery_separate(monkeypatch):
    job = SimpleNamespace(id=JOB, state=JobState.SUCCEEDED, progress=100, error=None, result=RESULT)
    monkeypatch.setattr(api, 'find_job', lambda _: job)
    response = api.TimelapseDetailView.as_view()(APIRequestFactory().get('/'), job_id=JOB)
    assert response.status_code == 200
    assert response.data['result'] == RESULT
    assert response.data['delivery'][0]['url'].endswith(f'/{JOB}/frames/0/')


def test_pending_frame_returns_conflict_without_s3(monkeypatch):
    monkeypatch.setattr(api, 'find_job', lambda _: SimpleNamespace(state=JobState.RUNNING))
    opener = Mock()
    monkeypatch.setattr(api, 'open_png', opener)
    response = api.FrameView.as_view()(APIRequestFactory().get('/'), job_id=JOB, index=0)
    assert response.status_code == 409
    opener.assert_not_called()


def test_png_stream_and_close(monkeypatch):
    monkeypatch.setattr(api, 'find_job', lambda _: SimpleNamespace(state=JobState.SUCCEEDED, result=RESULT))
    body = io.BytesIO(b'PNG')
    monkeypatch.setattr(api, 'open_png', lambda _: {'Body': body, 'ContentLength': 3})
    response = api.FrameView.as_view()(APIRequestFactory().get('/'), job_id=JOB, index=0)
    assert response['Content-Type'] == 'image/png'
    assert b''.join(response.streaming_content) == b'PNG'
    assert body.closed


def test_public_cors_preserves_spectral(monkeypatch):
    from django.http import HttpResponse

    from eon_common.cors import SpectralCorsMiddleware
    monkeypatch.delenv('EON_BROWSER_ORIGINS', raising=False)
    monkeypatch.delenv('EON_SPECTRAL_BROWSER_ORIGINS', raising=False)
    for service, path in [('spectral', '/api/v1/spectral/cubes/'), ('jobs', '/api/v1/timelapses/')]:
        with override_settings(SERVICE_NAME=service):
            response = SpectralCorsMiddleware(lambda _: HttpResponse())(APIRequestFactory().options(
                path, HTTP_ORIGIN='http://localhost:8080'))
            assert response.status_code == 204
            assert response['Access-Control-Allow-Origin'] == 'http://localhost:8080'
    with override_settings(SERVICE_NAME='jobs'):
        response = SpectralCorsMiddleware(lambda _: HttpResponse())(APIRequestFactory().options(
            '/api/v1/jobs/', HTTP_ORIGIN='http://localhost:8080'))
        assert not response.has_header('Access-Control-Allow-Origin')
