"""Bounded real validation of only the affected eon-stack-test Celery services."""
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

ROOT = Path('docs/evidence/celery_compose')
BASE = ['docker', 'compose', '-p', 'eon-stack-test']
PROTECTED = ['eon-postgres-local', 'eon-redis-local', 'eon-minio-local'] + [
    'eon-stack-test-' + name + '-1' for name in
    ['postgres', 'redis', 'minio', 'observation', 'image_processing', 'spectral', 'jobs', 'observation_worker', 'image_worker']]


def run(command, timeout=45):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError('Validation command failed: ' + str(result.returncode))
    return result.stdout


def snapshot(names):
    rows = json.loads(run(['docker', 'inspect', *names]))
    return {r['Name'].lstrip('/'): {'id': r['Id'], 'started_at': r['State']['StartedAt'],
                                   'running': r['State']['Running'], 'restarts': r['RestartCount']}
            for r in rows}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    evidence = {'checked_at_utc': datetime.now(UTC).isoformat(), 'passed': False, 'project': 'eon-stack-test'}
    before = snapshot(PROTECTED)
    evidence['protected_before'] = before
    try:
        run(BASE + ['config', '--quiet'])
        run(BASE + ['up', '-d', '--no-deps', 'spectral_worker', 'observation_beat'])
        time.sleep(5)
        beat_name = 'eon-stack-test-observation_beat-1'
        beat_before = snapshot([beat_name])
        code = '''
import json,uuid
from eon_common.celery import app
app.loader.import_default_modules()
i=app.control.inspect(timeout=5)
queues=i.active_queues() or {}
registered=i.registered() or {}
stats=i.stats() or {}
names=[name for name, q in queues.items() if any(v['name']=='spectral' for v in q)]
assert len(names)==1
worker=names[0]
required=['eon.spectral.prepare_cube','eon.spectral.extract_spectrum']
assert all(t in registered[worker] for t in required)
probe='compose-probe-'+uuid.uuid4().hex
r=app.send_task('celery.accumulate',args=[probe],queue='spectral',task_id=str(uuid.uuid4()))
value=r.get(timeout=25)
assert value==[probe]
out={'worker':worker,'queues':{n:[q['name'] for q in qs] for n,qs in queues.items()},'spectral_tasks':required,
     'pool':stats[worker]['pool']['implementation'],'concurrency':stats[worker]['pool']['max-concurrency'],
     'successful_broker_probe':{'task':'celery.accumulate','id':r.id,'state':r.state,'result':value},'spectral_dispatch':[]}
for task in required:
 r=app.send_task(task,kwargs={},queue='spectral',task_id=str(uuid.uuid4()))
 result=r.get(timeout=25,propagate=False)
 assert r.state=='FAILURE' and isinstance(result,TypeError)
 out['spectral_dispatch'].append({'task':task,'id':r.id,'state':r.state,'exception_type':type(result).__name__,
                                'note':'Intentionally missing required arguments; consumed and rejected before function body, no scientific download.'})
print(json.dumps(out))
'''
        worker_checks = json.loads(run(BASE + ['exec', '-T', 'spectral_worker', 'python', '-B', '-c', code], timeout=100))
        evidence['worker_checks'] = worker_checks
        assert any('celery' in q for n, q in worker_checks['queues'].items() if 'spectral' not in q)
        assert any('image_processing' in q for q in worker_checks['queues'].values())
        print('Spectral registration, real queue success and bounded actual-task rejection: Passed', flush=True)
        # Observe longer than the old restart cadence without a long blocking tool sleep.
        observed_start = time.monotonic()
        for _ in range(6):
            time.sleep(10)
            state = snapshot([beat_name])[beat_name]
            assert state['running'] and state['restarts'] == beat_before[beat_name]['restarts']
            assert state['started_at'] == beat_before[beat_name]['started_at']
        evidence['beat_observation_seconds'] = round(time.monotonic()-observed_start, 2)
        evidence['beat_before'] = beat_before
        evidence['beat_after'] = snapshot([beat_name])
        code = '''
import os,json,glob
from eon_common.celery import app
from django.conf import settings
p='/home/eon/beat/celerybeat-schedule'
assert os.access('/home/eon/beat',os.W_OK)
files=glob.glob(p+'*')
assert files and any(os.path.getsize(f)>0 for f in files)
entry=settings.CELERY_BEAT_SCHEDULE['sync-irsa']
assert entry['schedule']==86400.0
assert entry['task']=='services.observation.infrastructure.tasks.sync_irsa'
print(json.dumps({'uid':os.getuid(),'schedule_files':[os.path.basename(f) for f in files],
                  'schedule_directory_writable':True,'schedule':entry}))
'''
        evidence['beat_state'] = json.loads(run(BASE + ['exec', '-T', 'observation_beat', 'python', '-B', '-c', code]))
        evidence['beat_logs'] = [line for line in run(BASE + ['logs', '--tail', '30', '--no-color', 'observation_beat']).splitlines()
                                 if 'beat: Starting' in line or '. db ->' in line or 'PersistentScheduler' in line]
        session = requests.Session()
        session.trust_env = False
        evidence['http'] = {}
        for port in [8001, 8002, 8003, 8004, 19000]:
            path = '/minio/health/live' if port==19000 else '/api/v1/schema/'
            response = session.get(f'http://127.0.0.1:{port}{path}', timeout=(3, 10))
            assert response.status_code==200
            evidence['http'][str(port)] = response.status_code
        evidence['protected_after'] = snapshot(PROTECTED)
        assert evidence['protected_after']==before
        evidence['protected_unchanged'] = True
        evidence['passed'] = True
        print('Beat persistence/stability, all APIs, MinIO and original service preservation: Passed', flush=True)
    finally:
        (ROOT / 'validation.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
