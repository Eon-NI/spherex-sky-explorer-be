"""Real HTTP validation on an owned temporary Observation process; no FITS downloads."""
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

import requests

from services.image_processing.domain.security import validate_cutout_url
from services.image_processing.infrastructure.clients import ObservationApiResolver


def main():
    root=Path(__file__).resolve().parents[1]
    output=root/'docs/evidence/TAP_discovery_fix';output.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy()
    for line in (root/'.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):continue
        key,value=line.split('=',1);key=key.strip()
        if key.startswith(('POSTGRES_','DJANGO_')) or key=='REDIS_URL':
            env[key]=value.strip().strip(chr(34)).strip(chr(39))
    env.update(SERVICE_NAME='observation',POSTGRES_HOST='127.0.0.1',REDIS_URL='redis://127.0.0.1:6379/0',
               DJANGO_DEBUG='false',DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost',
               NO_PROXY='127.0.0.1,localhost',no_proxy='127.0.0.1,localhost')
    base='http://127.0.0.1:18001'
    session=requests.Session();session.trust_env=False
    try:
        session.get(base+'/health/',timeout=1)
    except requests.ConnectionError:pass
    else:raise RuntimeError('Validation port already in use; no process was started')
    report={'base_url':base,'passed':False,'kind':'Real Observation HTTP + PostgreSQL/Redis + IRSA discovery; no FITS/S3/jobs','requests':[]}
    with tempfile.TemporaryDirectory(prefix='eon-tap-validation-') as temp:
        log_path=Path(temp)/'server.log'
        with log_path.open('w',encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,'-B',str(root/'manage.py'),'runserver','127.0.0.1:18001','--noreload'],
                                     cwd=root,env=env,stdout=log,stderr=log,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(30):
                if process.poll() is not None:raise RuntimeError('Temporary Observation server exited')
                try:
                    if session.get(base+'/health/',timeout=1).status_code==200:break
                except requests.ConnectionError:pass
                time.sleep(.2)
            else:raise RuntimeError('Temporary Observation server not ready')
            os.environ.update(OBSERVATION_SERVICE_URL=base,NO_PROXY=env['NO_PROXY'],no_proxy=env['no_proxy'])
            for limit in [1,5]:
                params={'ra_deg':210.80227,'dec_deg':54.34895,'size_deg':.1,'detector':'SPHEREx-D2','limit':limit}
                t=time.monotonic()
                response=session.get(base+'/api/v1/observations/temporal/',params=params,timeout=(3,240))
                record={'limit':limit,'http_status':response.status_code,'elapsed_seconds':round(time.monotonic()-t,3),
                        'url':response.url,'response':response.json()}
                report['requests'].append(record)
                print('HTTP temporal limit',limit,'status',response.status_code,'elapsed',record['elapsed_seconds'],flush=True)
                response.raise_for_status()
                rows=response.json()['results']
                assert len(rows)==limit and len({r['product_id'] for r in rows})==limit
                assert [r['time_bounds_lower'] for r in rows]==sorted(r['time_bounds_lower'] for r in rows)
                for row in rows:
                    assert row['detector']=='SPHEREx-D2'
                    validate_cutout_url(row['cutout_url'],row['artifact_uri'],210.80227,54.34895,.1)
                    resolved=ObservationApiResolver().get(row['product_id'])
                    assert resolved is not None and asdict(resolved)==row
                record['all_details_and_be02_resolver_verified']=True
                print('All detail objects match SourceProduct/resolver',flush=True)
            report['passed']=True
        except Exception as exc:  # noqa: BLE001 - validation must preserve failed-stage evidence.
            report['error_type']=type(exc).__name__
            print('HTTP validation failed:',type(exc).__name__,flush=True)
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            lines=log_path.read_text(encoding='utf-8',errors='replace').splitlines()
            report['safe_discovery_logs']=[line for line in lines if any(term in line for term in
                    ['IRSA TAP query failed','TAP transport unavailable','SIA2 temporal point discovery'])]
    (output/'http_validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('Real discovery/detail validation passed:',report['passed'],flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
