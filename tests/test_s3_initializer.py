"""Bucket initializer guards: existing data/auth failures and bounded readiness."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

import pytest
from botocore.exceptions import ClientError

spec = importlib.util.spec_from_file_location('init_s3', Path(__file__).parents[1] / 'docker/init_s3.py')
initializer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(initializer)


def error(status, code):
    return ClientError({'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}, 'HeadBucket')


def test_existing_bucket_is_not_recreated():
    client = Mock()
    initializer.ensure_bucket(client, 'example')
    client.create_bucket.assert_not_called()


def test_missing_bucket_created_and_verified():
    client = Mock()
    client.head_bucket.side_effect = [error(404, 'NoSuchBucket'), None]
    initializer.ensure_bucket(client, 'example')
    client.create_bucket.assert_called_once_with(Bucket='example')
    assert client.head_bucket.call_count == 2


@pytest.mark.parametrize('status,code', [(403, 'AccessDenied'), (500, 'InternalError')])
def test_auth_and_service_errors_do_not_create_bucket(status, code):
    client = Mock()
    client.head_bucket.side_effect = error(status, code)
    with pytest.raises(ClientError):
        initializer.ensure_bucket(client, 'example')
    client.create_bucket.assert_not_called()


@pytest.mark.parametrize('code,accepted', [('BucketAlreadyOwnedByYou', True), ('BucketAlreadyExists', False)])
def test_creation_race_accepts_only_owned_bucket(code, accepted):
    client = Mock()
    client.head_bucket.side_effect = [error(404, 'NoSuchBucket'), None]
    client.create_bucket.side_effect = error(409, code)
    if accepted:
        initializer.ensure_bucket(client, 'example')
    else:
        with pytest.raises(ClientError):
            initializer.ensure_bucket(client, 'example')


def test_readiness_failure_has_finite_attempts(monkeypatch):
    opener = Mock()
    opener.open.side_effect = URLError('not ready')
    monkeypatch.setattr(initializer, 'build_opener', lambda *args: opener)
    sleeper = Mock()
    monkeypatch.setattr(initializer, 'sleep', sleeper)
    with pytest.raises(TimeoutError):
        initializer.wait_ready('http://minio:9000', attempts=3, delay=2)
    assert opener.open.call_count == 3
    assert sleeper.call_count == 2
