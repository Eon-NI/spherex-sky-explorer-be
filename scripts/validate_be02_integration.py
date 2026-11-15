"""Bounded real BE-02 validation with stage evidence; no scientific fixture fallback."""
import argparse
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import requests
from PIL import Image

from services.image_processing.application.timelapse import ProcessTimelapse
from services.image_processing.domain.models import ImageReference
from services.image_processing.domain.timelapse import TimelapseRequest
from services.image_processing.infrastructure.clients import ObservationApiResolver
from services.image_processing.infrastructure.downloader import IrsaCutoutDownloader
from services.image_processing.infrastructure.storage import S3ScientificObjectStore
from services.image_processing.infrastructure.timelapse import FitsSequenceRenderer


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=Path('docs/evidence/BE02_integration'))
    parser.add_argument('--load-env',action='store_true',help='Read S3 credentials from .env if absent, process memory only')
    args=parser.parse_args()
    if args.load_env:
        for line in Path('.env').read_text(encoding='utf-8-sig').splitlines():
            if '=' not in line or line.lstrip().startswith('#'): continue
            key,value=line.split('=',1)
            if key.strip() in {'S3_ACCESS_KEY','S3_SECRET_KEY'}:
                os.environ.setdefault(key.strip(),value.strip().strip(chr(34)).strip(chr(39)))
    os.environ.update(S3_ENDPOINT_URL='http://127.0.0.1:9000',S3_BUCKET='eon-science',
                      OBSERVATION_SERVICE_URL='http://127.0.0.1:8001',
                      NO_PROXY='127.0.0.1,localhost',no_proxy='127.0.0.1,localhost')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    evidence={'checked_at_utc':datetime.now(UTC).isoformat(),'end_to_end_passed':False,
              'endpoint':'http://127.0.0.1:9000','bucket':'eon-science','stages':{}}
    session=requests.Session(); session.trust_env=False
    store=S3ScientificObjectStore()
    stage='preflight'
    try:
        for name,url in [('observation_health','http://127.0.0.1:8001/health/'),
                         ('minio_health','http://127.0.0.1:9000/minio/health/live')]:
            stage=name
            r=session.get(url,timeout=(3,5));r.raise_for_status()
            evidence['stages'][name]={'status':'passed','http_status':r.status_code}
            print(name+': passed',flush=True)
        stage='s3_bucket'
        store.client.head_bucket(Bucket=store.bucket)
        evidence['stages'][stage]={'status':'passed'}
        # Independent storage check uses the existing, explicitly offline real PNG previews.
        stage='storage_only'
        prefix='validation/be02-storage-'+uuid.uuid4().hex
        objects=[]
        with tempfile.TemporaryDirectory(prefix='be02-s3-check-') as temporary:
            for source in sorted(Path('docs/evidence/BE02_preview').glob('frame-*.png'))[:5]:
                ref=ImageReference(prefix+'/'+source.name,'image/png')
                store.upload_file(source,ref)
                dest=Path(temporary)/source.name
                store.download_file(ref,dest)
                assert sha256(dest.read_bytes()).digest()==sha256(source.read_bytes()).digest()
                with Image.open(dest) as image:
                    image.verify()
                with Image.open(dest) as image:
                    assert image.format=='PNG' and image.size==(512,560)
                head=store.client.head_object(Bucket=store.bucket,Key=ref.object_key)
                assert head['ContentType']=='image/png'
                objects.append({'object_key':ref.object_key,'bytes':head['ContentLength'],
                                'content_type':head['ContentType'],'retrieval_identical':True,
                                'sha256':sha256(dest.read_bytes()).hexdigest(),'png_dimensions':[512,560]})
        assert len(objects)==5
        evidence['stages'][stage]={'status':'passed','objects':objects,
                                   'note':'Existing real-data previews only; not a new Observation processing run.'}
        print('Storage-only: five real PNGs uploaded and retrieved, byte-identical',flush=True)
        stage='temporal_discovery'
        params={'ra_deg':210.80227,'dec_deg':54.34895,'size_deg':0.1,'detector':'SPHEREx-D2','limit':5}
        print('Requesting at most five temporal products through Observation...',flush=True)
        r=session.get('http://127.0.0.1:8001/api/v1/observations/temporal/',params=params,timeout=(3,90))
        evidence['stages'][stage]={'status':'passed' if r.ok else 'failed','http_status':r.status_code,
                                   'url':r.url,'response':r.json()}
        r.raise_for_status()
        products=r.json()['results']
        if not 1<=len(products)<=5: raise ValueError('No suitable bounded Observation products')
        payload={'job_id':'be02-validation-'+uuid.uuid4().hex,'product_ids':[p['product_id'] for p in products],
                 'ra_deg':210.80227,'dec_deg':54.34895,'size_deg':0.1,'detector':'SPHEREx-D2'}
        (args.output_dir/'request.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
        request=TimelapseRequest(**payload)
        # Delegate to the real production adapters while recording exact failure stage.
        resolver=ObservationApiResolver();downloader=IrsaCutoutDownloader();renderer=FitsSequenceRenderer()
        class Resolver:
            def get(self,pid):
                nonlocal stage
                stage='product_resolution'
                value=resolver.get(pid)
                if value is not None: evidence['stages'].setdefault(stage,{'status':'passed','ids':[]})['ids'].append(pid)
                return value
        class Downloader:
            def download(self,url,destination,max_bytes):
                nonlocal stage
                stage='fits_acquisition'
                n=downloader.download(url,destination,max_bytes)
                evidence['stages'].setdefault(stage,{'status':'passed','files':[]})['files'].append({'bytes':destination.stat().st_size,'url':url})
                return n
        class Renderer:
            def render(self,inputs,req,directory):
                nonlocal stage
                stage='fits_wcs_png_processing'
                result=renderer.render(inputs,req,directory)
                evidence['stages'][stage]={'status':'passed','count':len(result[0])}
                return result
        class Storage:
            def upload_file(self,path,reference):
                nonlocal stage
                stage='output_uploads'
                store.upload_file(path,reference)
                evidence['stages'].setdefault(stage,{'status':'passed','objects':[]})['objects'].append(reference.object_key)
        print('Running real processing pipeline...',flush=True)
        manifest=ProcessTimelapse(Resolver(),Downloader(),Storage(),Renderer()).execute(request)
        stage='retrieval_and_manifest'
        assert manifest['job_id']==request.job_id and manifest['status']=='completed'
        assert 1<=len(manifest['frames'])<=5
        times=[]
        with tempfile.TemporaryDirectory(prefix='be02-retrieve-') as temporary:
            for index,frame in enumerate(manifest['frames']):
                assert {'index','product_id','timestamp','object_key'}<=set(frame)
                assert frame['index']==index and frame['product_id'] in request.product_ids
                assert frame['timestamp'].endswith('Z')
                assert frame['object_key'].startswith(request.prefix+'/')
                times.append(datetime.fromisoformat(frame['timestamp']))
                dest=Path(temporary)/f'{index}.png'
                store.download_file(ImageReference(frame['object_key'],'image/png'),dest)
                with Image.open(dest) as image: image.verify()
                with Image.open(dest) as image: assert image.format=='PNG' and image.size==(512,560)
                assert store.client.head_object(Bucket=store.bucket,Key=frame['object_key'])['ContentType']=='image/png'
            assert times==sorted(times)
            dest=Path(temporary)/'manifest.json'
            store.download_file(ImageReference(request.prefix+'/manifest.json','application/json'),dest)
            assert json.loads(dest.read_text(encoding='utf-8'))==manifest
        (args.output_dir/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        evidence['stages'][stage]={'status':'passed','frame_count':len(manifest['frames'])}
        evidence['end_to_end_passed']=True
    except Exception as exc:  # noqa: BLE001 - CLI records the precise failed stage for any adapter error.
        prior=evidence['stages'].get(stage,{})
        evidence['stages'][stage]={**prior,'status':'failed','error_type':type(exc).__name__}
        evidence['failed_stage']=stage
        for name in ['product_resolution','fits_acquisition','fits_wcs_png_processing','output_uploads','retrieval_and_manifest']:
            evidence['stages'].setdefault(name,{'status':'not_run'})
        print('Validation failed at '+stage+' ('+type(exc).__name__+')',flush=True)
    (args.output_dir/'validation.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print('End-to-end passed: '+str(evidence['end_to_end_passed']),flush=True)
    return 0 if evidence['end_to_end_passed'] else 1


if __name__=='__main__': raise SystemExit(main())
