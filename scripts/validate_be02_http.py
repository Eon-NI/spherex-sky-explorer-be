"""One real HTTP cutout; bounded polling; never starts or restarts a service."""
import io
import json
import os
import re
import time
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import jsonschema
import numpy as np
import psycopg
import requests
import yaml
from astropy.io import fits
from astropy.wcs import WCS
from redis import Redis

ROOT = Path('docs/evidence/BE02_http_validation')
API = 'http://127.0.0.1:8002'
OBS = 'http://127.0.0.1:8001'
STAGES = ['postgresql', 'redis', 'observation_health', 'image_health', 'jobs_health',
          'minio_health', 's3_bucket', 'celery_worker', 'openapi', 'discovery',
          'product_detail', 'submission', 'async_execution', 'metadata', 'fits_download',
          'scientific_validation', 'minio_retrieval', 'schema_comparison']


def main():
    # Existing secrets only; localhost overrides are limited to this diagnostic process.
    for line in Path('.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        if key.strip() in {'POSTGRES_USER', 'POSTGRES_PASSWORD', 'S3_ACCESS_KEY', 'S3_SECRET_KEY'}:
            os.environ.setdefault(key.strip(), value.strip().strip(chr(34)).strip(chr(39)))
    os.environ.update(SERVICE_NAME='image_processing', DJANGO_SETTINGS_MODULE='eon_common.settings',
                      REDIS_URL='redis://127.0.0.1:6379/0', S3_ENDPOINT_URL='http://127.0.0.1:9000',
                      S3_BUCKET='eon-science', NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
    ROOT.mkdir(parents=True, exist_ok=True)
    evidence = {'checked_at_utc': datetime.now(UTC).isoformat(), 'http_cutout_flow_passed': False,
                'native_tap_verified': False, 'stages': {}, 'poll_timeout_seconds': 240,
                'notes': ['No services started/restarted; at most one POST, no submission retry.',
                          'Fresh BE-01 HTTP request; cache/upstream discovery route is not exposed.',
                          'Separate from application timelapse and BE-03 frontend validation.']}
    session = requests.Session()
    session.trust_env = False
    stage = 'postgresql'

    def passed(name, **details):
        evidence['stages'][name] = {'status': 'Passed', **details}
        print(name + ': Passed', flush=True)

    def get_json(url, timeout=(3, 15)):
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        return response, response.json()

    def save(name, value):
        (ROOT / name).write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')

    try:
        with psycopg.connect(host='127.0.0.1', port=5432, dbname='eon_image_processing',
                             user=os.environ.get('POSTGRES_USER', 'eon'),
                             password=os.environ.get('POSTGRES_PASSWORD', ''), connect_timeout=5) as connection:
            assert connection.execute('SELECT 1').fetchone()[0] == 1
        passed(stage, database='eon_image_processing', query='SELECT 1')
        stage = 'redis'
        broker = Redis.from_url(os.environ['REDIS_URL'], socket_timeout=5, socket_connect_timeout=3)
        assert broker.ping()
        passed(stage, endpoint='127.0.0.1:6379/0')
        for stage, url in [('observation_health', OBS + '/health/'), ('image_health', API + '/health/'),
                           ('jobs_health', 'http://127.0.0.1:8004/health/'),
                           ('minio_health', 'http://127.0.0.1:9000/minio/health/live')]:
            try:
                response = session.get(url, timeout=(3, 5))
                response.raise_for_status()
                passed(stage, http_status=response.status_code)
            except requests.RequestException as exc:
                if stage != 'jobs_health':
                    raise
                evidence['stages'][stage] = {'status': 'Failed', 'error_type': type(exc).__name__, 'url': url}
                print('jobs_health: Failed (' + type(exc).__name__ + ')', flush=True)
        stage = 's3_bucket'
        from services.image_processing.infrastructure.storage import S3ScientificObjectStore
        store = S3ScientificObjectStore()
        store.client.head_bucket(Bucket=store.bucket)
        passed(stage, bucket=store.bucket)
        stage = 'celery_worker'
        from eon_common.celery import app
        inspect = app.control.inspect(timeout=5)
        queues, registered = inspect.active_queues() or {}, inspect.registered() or {}
        eligible = [name for name, values in queues.items()
                    if any(q['name'] == 'image_processing' for q in values)
                    and 'eon.image_processing.create_cutout' in registered.get(name, [])]
        assert eligible, 'No registered create_cutout consumer on image_processing'
        passed(stage, workers=eligible, queue='image_processing', task='eon.image_processing.create_cutout')
        stage = 'openapi'
        response = session.get(API + '/api/v1/schema/', timeout=(3, 15))
        response.raise_for_status()
        schema = yaml.safe_load(response.text)
        save('openapi.json', schema)
        passed(stage, http_status=response.status_code, version=schema['openapi'])
        stage = 'discovery'
        params = {'ra_deg': 210.80227, 'dec_deg': 54.34895, 'size_deg': 0.1, 'detector': 'SPHEREx-D2', 'limit': 1}
        response, discovery = get_json(OBS + '/api/v1/observations/temporal/?' + requests.compat.urlencode(params), (3, 90))
        save('discovery.json', discovery)
        assert discovery['count'] == 1 and len(discovery['results']) == 1
        product = discovery['results'][0]
        pid = product['product_id']
        assert re.fullmatch('[0-9a-f]{64}', pid)
        passed(stage, http_status=response.status_code, url=response.url, product_id=pid)
        stage = 'product_detail'
        response, detail = get_json(OBS + f'/api/v1/observations/temporal/{pid}/')
        assert product == detail
        save('product.json', detail)
        passed(stage, http_status=response.status_code)
        payload = {k: product[k] for k in ['product_id', 'ra_deg', 'dec_deg', 'size_deg']}
        save('request.json', payload)
        stage = 'submission'
        if evidence['stages']['jobs_health']['status'] != 'Passed':
            evidence['stages'][stage] = {'status': 'Blocked', 'reason': 'Jobs Service at port 8004 is unreachable; no POST submitted.'}
            evidence['blocking_stage'] = 'jobs_health'
            print('submission: Blocked (Jobs unavailable; no job submitted)', flush=True)
            return 1
        response = session.post(API + '/api/v1/cutouts/', json=payload, timeout=(3, 30))
        try:
            submission = response.json()
        except ValueError:
            submission = {'non_json_response': True}
        save('submission.json', {'http_status': response.status_code, 'response': submission})
        assert response.status_code == 202, 'POST did not return HTTP 202'
        job_id = submission['job_id']
        uuid.UUID(job_id)
        status_path = submission['status_url']
        assert status_path == f'/api/v1/cutouts/jobs/{job_id}/'
        passed(stage, http_status=202, job_id=job_id, status_url=status_path)
        stage = 'async_execution'
        deadline, polls = time.monotonic() + 240, []
        for _ in range(80):
            if time.monotonic() >= deadline:
                break
            response, status = get_json(API + status_path, (3, min(10, max(1, deadline-time.monotonic()))))
            polls.append({'http_status': response.status_code, 'response': status})
            save('polls.json', polls)
            assert status['id'] == job_id
            if status['state'] in {'succeeded', 'failed'}:
                break
            time.sleep(min(3, max(0, deadline-time.monotonic())))
        assert status['state'] == 'succeeded', 'Job did not reach succeeded; see polls.json'
        result = status['result']
        cutout_id = result['cutout_id']
        assert re.fullmatch('[0-9a-f]{64}', cutout_id)
        object_key = result['object_key']
        passed(stage, state=status['state'], poll_count=len(polls), cutout_id=cutout_id, object_key=object_key,
               note='A first-poll success may be idempotent reuse; no fresh execution inferred from that alone.')
        stage = 'metadata'
        assert result['metadata_url'] == f'/api/v1/cutouts/{cutout_id}/metadata/'
        response, metadata = get_json(API + result['metadata_url'], (3, 45))
        save('metadata.json', metadata)
        assert metadata['cutout_id'] == cutout_id and metadata['object_key'] == object_key
        assert metadata['source_url'] == product['cutout_url']
        assert metadata['time_bounds_lower'] == product['time_bounds_lower']
        passed(stage, http_status=response.status_code)
        stage = 'fits_download'
        with session.get(API + f'/api/v1/cutouts/{cutout_id}/fits/', stream=True, timeout=(3, 45)) as response:
            response.raise_for_status()
            assert response.headers['Content-Type'].split(';')[0] == 'application/fits'
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                assert len(data) <= 8*1024*1024
            if 'Content-Length' in response.headers:
                assert int(response.headers['Content-Length']) == len(data)
        passed(stage, http_status=response.status_code, bytes=len(data), sha256=sha256(data).hexdigest(), content_type='application/fits')
        stage = 'scientific_validation'
        from dataclasses import asdict
        from tempfile import TemporaryDirectory

        from services.image_processing.infrastructure.fits_reader import AstropyFitsReader
        from services.image_processing.infrastructure.timelapse import acquisition_time
        with TemporaryDirectory(prefix='be02-http-fits-') as directory:
            path = Path(directory) / 'cutout.fits'
            path.write_bytes(data)
            read = asdict(AstropyFitsReader().inspect(path, product['ra_deg'], product['dec_deg']))
            assert json.loads(json.dumps(read)) == metadata['fits']
        with fits.open(io.BytesIO(data), memmap=False) as hdul:
            hdul.verify('exception')
            image = hdul['IMAGE']
            header = hdul[0].header.copy()
            header.update(image.header)
            assert str(header['DETECTOR']) == '2'
            obsid = str(header['OBSID'])
            assert f'level2_{obsid}D2_' in product['artifact_uri']
            assert image.data.ndim == 2 and np.isfinite(image.data).any()
            assert WCS(image.header, fobj=hdul).celestial.has_celestial
            observation_time, time_meta = acquisition_time(header)
            assert abs(observation_time.mjd-product['time_bounds_lower'])*86400 < 1
            assert read['wavelength_um'] > 0 and read['bandwidth_um'] > 0
            assert read['image_statistics']['finite_pixels'] > 0
            passed(stage, obs_id=obsid, detector=read['detector'], image_shape=read['image_shape'],
                   image_unit=read['image_unit'], timestamp=observation_time.isot+'Z', time_provenance=time_meta,
                   wavelength_um=read['wavelength_um'], bandwidth_um=read['bandwidth_um'],
                   metadata_matches_download=True, region_roundtrip=read['sky_position_deg'])
        stage = 'minio_retrieval'
        obj = store.client.get_object(Bucket=store.bucket, Key=object_key)
        try:
            stored = obj['Body'].read(8*1024*1024+1)
        finally:
            obj['Body'].close()
        assert stored == data
        passed(stage, bucket=store.bucket, object_key=object_key, bytes=len(stored),
               sha256=sha256(stored).hexdigest(), byte_identical=True, content_type=obj['ContentType'])
        stage = 'schema_comparison'
        # Convert OpenAPI 3.0 nullable into JSON Schema, preserving local $ref targets.
        def convert(value):
            if isinstance(value, list):
                return [convert(item) for item in value]
            if not isinstance(value, dict):
                return value
            converted = {k: convert(v) for k, v in value.items() if k != 'nullable'}
            if value.get('nullable'):
                return {'anyOf': [converted, {'type': 'null'}]}
            return converted
        document = convert(schema)
        validator = jsonschema.Draft4Validator(document)
        def check(value, specification):
            validator.evolve(schema=convert(specification)).validate(value)
        post = schema['paths']['/api/v1/cutouts/']['post']
        check(payload, post['requestBody']['content']['application/json']['schema'])
        check(submission, post['responses']['202']['content']['application/json']['schema'])
        poll_schema = schema['paths']['/api/v1/cutouts/jobs/{job_id}/']['get']['responses']['200']['content']['application/json']['schema']
        for item in polls:
            check(item['response'], poll_schema)
        check(metadata, schema['paths']['/api/v1/cutouts/{cutout_id}/metadata/']['get']['responses']['200']['content']['application/json']['schema'])
        assert 'application/fits' in schema['paths']['/api/v1/cutouts/{cutout_id}/fits/']['get']['responses']['200']['content']
        passed(stage, discrepancies=[], note='Nested result/fits are generic dictionaries in the published schema; scientific assertions checked separately.')
        evidence['http_cutout_flow_passed'] = True
    except Exception as exc:  # noqa: BLE001 - preserve failed-stage evidence without leaking credentials
        evidence['stages'][stage] = {'status': 'Failed', 'error_type': type(exc).__name__}
        evidence['failed_stage'] = stage
        # Public API payloads are saved separately; never serialize raw credential-bearing adapter errors.
        print(stage + ': Failed (' + type(exc).__name__ + ')', flush=True)
    finally:
        for name in STAGES:
            evidence['stages'].setdefault(name, {'status': 'Blocked', 'reason': 'Prerequisite failed'})
        save('validation.json', evidence)
    print('http_cutout_flow_passed=' + str(evidence['http_cutout_flow_passed']), flush=True)
    return 0 if evidence['http_cutout_flow_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
