"""Isolated real Compose/MinIO check. Never deletes a volume or touches existing services."""
import json
import secrets
import socket
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import boto3
import requests
from botocore.config import Config


def main():
    root = Path('docs/evidence/compose_minio')
    root.mkdir(parents=True, exist_ok=True)
    project = 'eon-minio-check-' + uuid.uuid4().hex[:8]
    evidence = {'checked_at_utc': datetime.now(UTC).isoformat(), 'project': project,
                'passed': False, 'stages': {}, 'volumes_deleted': False}
    names = ['eon-postgres-local', 'eon-redis-local', 'eon-minio-local']

    def run(args, timeout=120):
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
        if result.returncode:
            # Docker build errors do not receive credentials on the CLI; retain only a short failure tail.
            evidence['command_failure'] = {'command': args[:4], 'exit_code': result.returncode,
                                          'output_tail': result.stderr[-2500:]}
            raise RuntimeError('Docker command failed')
        return result.stdout

    def snapshot():
        rows = json.loads(run(['docker', 'inspect', *names]))
        return {row['Name'].lstrip('/'): {'id': row['Id'], 'started_at': row['State']['StartedAt'],
                                         'running': row['State']['Running'],
                                         'mounts': [{k: m.get(k) for k in ['Type', 'Name', 'Destination']} for m in row['Mounts']]}
                for row in rows}

    stage = 'preflight'
    before = snapshot()
    evidence['existing_containers_before'] = before
    for port in [19000, 19001]:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    evidence['stages'][stage] = 'Passed: existing service identities recorded, test ports available'
    with tempfile.TemporaryDirectory(prefix='eon-compose-s3-') as temporary:
        envfile = Path(temporary) / 'validation.env'
        secret = secrets.token_urlsafe(32)
        envfile.write_text('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24) + '\nS3_ACCESS_KEY=eonvalidation\nS3_SECRET_KEY=' + secret + '\nS3_BUCKET=eon-compose-validation\nMINIO_API_PORT=19000\nMINIO_CONSOLE_PORT=19001\n', encoding='utf-8')
        base = ['docker', 'compose', '-p', project, '--env-file', str(envfile)]
        started = False
        try:
            stage = 'compose_config'
            run(base + ['config', '--quiet'])
            evidence['stages'][stage] = 'Passed'
            print('Compose config: Passed', flush=True)
            stage = 'nonempty_volume_guard'
            started = True
            # Seed only this fresh project volume; verify failure preserves both file and owner.
            run(base + ['run', '--rm', '--no-deps', '--entrypoint', '/bin/sh', 'minio_volume_init',
                        '-ec', 'echo preserve > /data/permission-guard-sentinel'])
            guard = subprocess.run(base + ['run', '--rm', '--no-deps', 'minio_volume_init'],
                                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30, check=False)
            assert guard.returncode != 0 and 'Refusing ownership changes' in guard.stderr
            run(base + ['run', '--rm', '--no-deps', '--entrypoint', '/bin/sh', 'minio_volume_init', '-ec',
                        ('test "$(cat /data/permission-guard-sentinel)" = preserve; '
                        'test "$(stat -c %u:%g /data)" = 0:0; rm /data/permission-guard-sentinel')])
            evidence['stages'][stage] = 'Passed: wrong-owner nonempty volume refused; sentinel and ownership unchanged'
            stage = 'build_and_start'
            started = True
            run(base + ['up', '--build', '-d', 'minio_setup'], timeout=600)
            evidence['stages'][stage] = 'Passed'
            print('Isolated server and initializer started', flush=True)
            stage = 'initializer'
            setup_id = run(base + ['ps', '-a', '-q', 'minio_setup']).strip()
            # up waits on MinIO health; wait separately for the one-shot initializer to exit.
            exit_code = run(['docker', 'wait', setup_id], timeout=120).strip()
            assert exit_code == '0'
            logs = run(base + ['logs', '--no-color', 'minio_setup'])
            assert 'S3 bucket ready' in logs
            evidence['stages'][stage] = 'Passed: one-shot exit 0 and bucket ready'
            stage = 'nonroot_volume'
            server_id = run(base + ['ps', '-q', 'minio']).strip()
            server = json.loads(run(['docker', 'inspect', server_id]))[0]
            assert server['Config']['User'] == '100:101'
            assert server['State']['Health']['Status'] == 'healthy'
            permissions = run(base + ['exec', '-T', 'minio', '/bin/sh', '-ec', 'id; stat -c "%u:%g" /data; test -w /data'])
            assert 'uid=100' in permissions and '100:101' in permissions
            evidence['stages'][stage] = 'Passed: server UID 100/GID 101; /data writable and health=healthy'
            evidence['test_volume'] = next(m['Name'] for m in server['Mounts'] if m['Destination'] == '/data')
            evidence['image'] = server['Config']['Image']
            stage = 'health'
            session = requests.Session()
            session.trust_env = False
            response = session.get('http://127.0.0.1:19000/minio/health/ready', timeout=(3, 5))
            response.raise_for_status()
            evidence['stages'][stage] = {'status': 'Passed', 'http_status': response.status_code}
            stage = 's3_roundtrip'
            client = boto3.client('s3', endpoint_url='http://127.0.0.1:19000',
                                  aws_access_key_id='eonvalidation', aws_secret_access_key=secret,
                                  region_name='us-east-1', config=Config(connect_timeout=3, read_timeout=5,
                                  retries={'total_max_attempts': 2}, s3={'addressing_style': 'path'}))
            bucket, key = 'eon-compose-validation', 'validation/roundtrip.txt'
            client.head_bucket(Bucket=bucket)
            content = b'EON COMPOSE S3 OK\n'
            client.put_object(Bucket=bucket, Key=key, Body=content, ContentType='text/plain')
            obj = client.get_object(Bucket=bucket, Key=key)
            try:
                assert obj['Body'].read() == content
            finally:
                obj['Body'].close()
            client.delete_object(Bucket=bucket, Key=key)
            assert client.list_objects_v2(Bucket=bucket).get('KeyCount', 0) == 0
            evidence['stages'][stage] = 'Passed: head bucket, put/get byte equality, delete, empty bucket'
            stage = 'idempotent_initializer'
            run(base + ['run', '--rm', '--no-deps', 'minio_setup'], timeout=120)
            evidence['stages'][stage] = 'Passed: existing bucket accepted without replacement'
            evidence['passed'] = True
            print('Health, non-root volume, bucket init, S3 roundtrip and repeated init: Passed', flush=True)
        except Exception as exc:  # noqa: BLE001 - record failure without raw credentials
            evidence['failed_stage'] = stage
            evidence['error_type'] = type(exc).__name__
            print('Failed at ' + stage + ': ' + type(exc).__name__, flush=True)
        finally:
            if started:
                run(base + ['down', '--timeout', '10'], timeout=90)
                evidence['isolated_containers_network_removed'] = True
            after = snapshot()
            evidence['existing_containers_after'] = after
            evidence['existing_containers_unchanged'] = before == after
            evidence['passed'] = evidence['passed'] and before == after
            (root / 'validation.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
