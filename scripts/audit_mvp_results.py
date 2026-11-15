"""Audit completed real MVP jobs and existing stored artifacts without reprocessing science."""
import io
import json
import os
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import boto3
import requests
from astropy.io import fits
from astropy.time import Time
from botocore.config import Config
from PIL import Image


def main():
    for line in Path('.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1)
            if k.strip() in {'S3_ACCESS_KEY','S3_SECRET_KEY','S3_BUCKET'}:
                os.environ.setdefault(k.strip(),v.strip().strip(chr(34)).strip(chr(39)))
    client=boto3.client('s3',endpoint_url='http://127.0.0.1:19000',aws_access_key_id=os.environ['S3_ACCESS_KEY'],
                        aws_secret_access_key=os.environ['S3_SECRET_KEY'],
                        config=Config(connect_timeout=3,read_timeout=15,retries={'total_max_attempts':2}))
    session=requests.Session();session.trust_env=False
    evidence={'passed':False,'jobs':[]}
    for job_id,count in [('c9585bf7-f7a6-4c17-b34e-1ea7a7fcd961',2),('a18d40b0-08fc-449f-ba31-7f47b71a48a3',5)]:
        response=session.get(f'http://localhost:8004/api/v1/timelapses/{job_id}/',timeout=(3,15));response.raise_for_status()
        status=response.json();assert status['status']=='completed'
        manifest=status['result'];assert manifest['job_id']==job_id and manifest['status']=='completed'
        frames=manifest['frames'];assert len(frames)==count
        times=[datetime.fromisoformat(f['timestamp']) for f in frames];assert times==sorted(times) and len(set(times))==count
        check={'job_id':job_id,'frame_count':count,'timestamps':[f['timestamp'] for f in frames],
               'grid':manifest['provenance']['grid'],'normalization':manifest['provenance']['normalization'],'frames':[]}
        assert check['grid']['shape']==[59,59]
        for index,(frame,meta) in enumerate(zip(frames,manifest['provenance']['observations'],strict=True)):
            assert frame['index']==index
            detail=session.get('http://localhost:8001/api/v1/observations/temporal/'+frame['product_id']+'/',timeout=(3,10));detail.raise_for_status()
            assert detail.json()['cutout_url']==meta['source_url']
            head=client.head_object(Bucket=os.environ['S3_BUCKET'],Key=meta['source_object_key'])
            assert 2880<=head['ContentLength']<=8*1024*1024
            delivered=session.get(status['delivery'][index]['url'],headers={'Origin':'http://localhost:8080'},timeout=(3,15));delivered.raise_for_status()
            assert delivered.headers['Content-Type']=='image/png' and delivered.headers['Access-Control-Allow-Origin']=='http://localhost:8080'
            with Image.open(io.BytesIO(delivered.content)) as image:
                assert image.size==(512,560) and image.format=='PNG'
                image.verify()
            obj=client.get_object(Bucket=os.environ['S3_BUCKET'],Key=frame['object_key'])
            try:assert obj['Body'].read()==delivered.content
            finally:obj['Body'].close()
            check['frames'].append({'product_id':frame['product_id'],'source_fits_bytes':head['ContentLength'],
                                     'png_bytes':len(delivered.content),'sha256':sha256(delivered.content).hexdigest(),'url':status['delivery'][index]['url']})
            if count==5 and index==0:
                obj=client.get_object(Bucket=os.environ['S3_BUCKET'],Key=meta['source_object_key'])
                try:data=obj['Body'].read(8*1024*1024+1)
                finally:obj['Body'].close()
                assert sha256(data).hexdigest()==meta['source_sha256']
                with fits.open(io.BytesIO(data),memmap=False) as hdul:
                    hdul.verify('exception')
                    header=hdul[0].header.copy();header.update(hdul['IMAGE'].header)
                    assert str(header['DETECTOR'])=='2'
                    assert header['OBSID']==meta['obs_id']
                    timestamp=Time(header['DATE-OBS'],format='isot',scale=str(header['TIMESYS']).lower()).utc.isot+'Z'
                    assert timestamp==frame['timestamp']
                check['source_fits_verified']=True
        obj=client.get_object(Bucket=os.environ['S3_BUCKET'],Key=manifest['provenance']['manifest_object_key'])
        try:assert json.loads(obj['Body'].read())==manifest
        finally:obj['Body'].close()
        evidence['jobs'].append(check)
    evidence['passed']=True
    root=Path('docs/evidence/mvp_integration');root.mkdir(parents=True,exist_ok=True)
    (root/'artifact_audit.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'jobs':[{'job_id':j['job_id'],'frames':j['frame_count'],'timestamps':j['timestamps']} for j in evidence['jobs']]}))


if __name__=='__main__':main()
