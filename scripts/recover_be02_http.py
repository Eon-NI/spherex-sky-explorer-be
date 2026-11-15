"""Recover missing local API/worker, validate HTTP cutout and clean up owned processes."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path('docs/evidence/BE02_http_validation')


def main():
    env = os.environ.copy()
    for line in Path('.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            env.setdefault(key.strip(), value.strip().strip(chr(34)).strip(chr(39)))
    env.update(SERVICE_NAME='image_processing', DJANGO_SETTINGS_MODULE='eon_common.settings',
               POSTGRES_HOST='127.0.0.1', POSTGRES_PORT='5432', POSTGRES_DB='eon_image_processing',
               REDIS_URL='redis://127.0.0.1:6379/0', OBSERVATION_SERVICE_URL='http://127.0.0.1:8001',
               JOB_SERVICE_URL='http://127.0.0.1:8004', S3_ENDPOINT_URL='http://127.0.0.1:9000',
               S3_BUCKET='eon-science', NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost',
               PYTHONDONTWRITEBYTECODE='1', EON_MAX_CUTOUT_BYTES=str(8*1024*1024))
    if not env.get('EON_INTERNAL_TOKEN'):
        raise RuntimeError('Existing internal token missing')
    os.environ.update(env)
    ROOT.mkdir(parents=True, exist_ok=True)
    lifecycle = {'owned_processes': [], 'existing_services_restarted': [], 'configuration_scope': 'child process environment only'}
    owned = []
    session = requests.Session()
    session.trust_env = False
    code = 1
    paused_workers = []
    with tempfile.TemporaryDirectory(prefix='eon-http-lifecycle-') as folder:
        logs = []
        def start(name, arguments):
            path = Path(folder) / (name + '.log')
            handle = path.open('w', encoding='utf-8')
            process = subprocess.Popen([sys.executable, '-B', *arguments], env=env,
                                       stdout=handle, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            owned.append((name, process, handle))
            logs.append((name, path))
            lifecycle['owned_processes'].append({'name': name, 'pid': process.pid, 'command': arguments})
            return process
        try:
            # Avoid accidental competition with another listener, including non-HTTP services.
            with socket.socket() as probe:
                occupied = probe.connect_ex(('127.0.0.1', 8002)) == 0
            if not occupied:
                api = start('image_api', ['manage.py', 'runserver', '127.0.0.1:8002', '--noreload'])
                ready = False
                for _ in range(30):
                    if api.poll() is not None:
                        break
                    try:
                        r = session.get('http://127.0.0.1:8002/health/', timeout=(1, 2))
                        ready = r.ok and r.json().get('service') == 'image_processing'
                        if ready:
                            break
                    except requests.RequestException:
                        pass
                    time.sleep(0.5)
                if not ready:
                    raise RuntimeError('Owned Image Processing API failed readiness')
                print('Missing Image Processing API started on :8002', flush=True)
            if '--error-checks-only' in sys.argv:
                audit_error_responses(session, env)
                lifecycle['error_checks_only'] = True
                return 0
            from redis import Redis

            from eon_common.celery import app
            inspect = app.control.inspect(timeout=3)
            queues, registered = inspect.active_queues() or {}, inspect.registered() or {}
            eligible = [name for name, entries in queues.items()
                        if any(q['name'] == 'image_processing' for q in entries)
                        and 'eon.image_processing.create_cutout' in registered.get(name, [])]
            lifecycle['existing_eligible_workers'] = eligible
            if '--isolate-worker' in sys.argv:
                for name in eligible:
                    target = app.control.inspect(destination=[name], timeout=3)
                    active, reserved, scheduled = target.active(), target.reserved(), target.scheduled()
                    if any(value is None or value.get(name) for value in [active, reserved, scheduled]):
                        raise RuntimeError('Existing worker is busy or cannot be inspected; unsafe to pause')
                    response = app.control.cancel_consumer('image_processing', destination=[name], reply=True, timeout=3)
                    if not response:
                        raise RuntimeError('Worker queue pause not acknowledged')
                    paused_workers.append(name)
                lifecycle['temporarily_paused_workers'] = paused_workers
                eligible = []
            if not eligible:
                broker = Redis.from_url(env['REDIS_URL'])
                if broker.llen('image_processing'):
                    raise RuntimeError('Unowned pending queue work prevents isolated worker startup')
                start('image_worker', ['-m', 'celery', '-A', 'eon_common.celery:app', 'worker',
                                      '--pool=solo', '--concurrency=1', '-Q', 'image_processing',
                                      '--hostname=eon-be02-validation@%h', '--loglevel=INFO',
                                      '--without-gossip', '--without-mingle'])
                for _ in range(10):
                    if app.control.inspect(timeout=2).ping():
                        break
                    time.sleep(0.5)
                print('Bounded solo Image Processing worker started', flush=True)
            command = [sys.executable, '-B', '-u', '-m', 'scripts.validate_be02_http']
            code = subprocess.run(command, env=env, timeout=420, check=False).returncode
            lifecycle['validation_exit_code'] = code
        finally:
            for name, process, handle in reversed(owned):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                handle.close()
                lifecycle.setdefault('cleanup', []).append({'name': name, 'pid': process.pid, 'stopped': process.poll() is not None})
            for name in paused_workers:
                response = app.control.add_consumer('image_processing', destination=[name], reply=True, timeout=3)
                lifecycle.setdefault('restored_workers', []).append({'name': name, 'acknowledged': bool(response)})
            # Keep only safe task/lifecycle summaries, not raw credential-bearing tracebacks.
            lifecycle['log_summaries'] = {}
            for name, path in logs:
                text = path.read_text(encoding='utf-8', errors='replace')
                lifecycle['log_summaries'][name] = [line for line in text.splitlines()
                    if ('"GET /' in line or '"POST /' in line or
                        ('Task eon.image_processing.create_cutout[' in line and
                         (' received' in line or ' succeeded' in line)))]
            (ROOT / 'lifecycle.json').write_text(json.dumps(lifecycle, indent=2), encoding='utf-8')
    return code


def audit_error_responses(session, env):
    import uuid

    import yaml

    product = json.loads((ROOT / 'request.json').read_text())
    wrong_region = {**product, 'size_deg': 0.09}
    checks = [
        ('missing_fields', 'POST', 'http://127.0.0.1:8002/api/v1/cutouts/', {}, {}, 400),
        ('mismatched_region', 'POST', 'http://127.0.0.1:8002/api/v1/cutouts/', wrong_region, {}, 400),
        ('unknown_job', 'GET', f'http://127.0.0.1:8002/api/v1/cutouts/jobs/{uuid.uuid4()}/', None, {}, 404),
        ('invalid_cutout', 'GET', 'http://127.0.0.1:8002/api/v1/cutouts/not-a-hash/metadata/', None, {}, 400),
        ('unknown_cutout', 'GET', 'http://127.0.0.1:8002/api/v1/cutouts/' + '0'*64 + '/metadata/', None, {}, 404),
        ('jobs_no_auth', 'POST', 'http://127.0.0.1:8004/api/v1/jobs/', {}, {}, 401),
        ('jobs_matching_token', 'POST', 'http://127.0.0.1:8004/api/v1/jobs/', {},
         {'X-EON-Internal-Token': env['EON_INTERNAL_TOKEN']}, 400),
    ]
    results = {}
    for name, method, url, payload, headers, expected in checks:
        response = session.request(method, url, json=payload, headers=headers, timeout=(3, 15))
        results[name] = {'http_status': response.status_code, 'expected': expected,
                         'response': response.json(), 'passed': response.status_code == expected}
    (ROOT / 'error_checks.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    assert all(item['passed'] for item in results.values())
    response = session.get('http://127.0.0.1:8004/api/v1/schema/', timeout=(3, 15))
    response.raise_for_status()
    jobs_schema = yaml.safe_load(response.text)
    (ROOT / 'jobs_openapi.json').write_text(json.dumps(jobs_schema, indent=2), encoding='utf-8')
    from services.image_processing.infrastructure.storage import S3ScientificObjectStore
    store = S3ScientificObjectStore()
    validation = json.loads((ROOT / 'validation.json').read_text())
    key = validation['stages']['minio_retrieval']['object_key']
    head = store.client.head_object(Bucket=store.bucket, Key=key)
    storage = {'object_key': key, 'last_modified_utc': head['LastModified'].isoformat(),
               'bytes': head['ContentLength'], 'content_type': head['ContentType']}
    (ROOT / 'object_head.json').write_text(json.dumps(storage, indent=2), encoding='utf-8')
    print('Seven non-enqueuing HTTP error/authentication checks: Passed', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
