"""Explicit OFFLINE preview evidence; never claims BE-01 or S3 integration.

Uses real cached PoC FITS matched by URL hash to a supplied local SIA VOTable.
--download-one optionally verifies one fresh IRSA acquisition, still not BE-01.
"""
import argparse
import json
import tempfile
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from astropy.io.votable import parse

from services.image_processing.domain.models import SourceProduct
from services.image_processing.domain.security import validate_cutout_url
from services.image_processing.domain.timelapse import TimelapseRequest
from services.image_processing.infrastructure.downloader import IrsaCutoutDownloader
from services.image_processing.infrastructure.timelapse import FitsSequenceRenderer


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fits-dir',type=Path,required=True)
    parser.add_argument('--sia-xml',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--maximum',type=int,choices=range(1,11),default=5)
    parser.add_argument('--download-one',action='store_true')
    args=parser.parse_args()
    table=next(t for t in parse(str(args.sia_xml)).iter_tables() if 'access_url' in [f.name for f in t.fields]).to_table(use_names_over_ids=True)
    inputs=[]
    for row in table:
        if str(row['energy_bandpassname'])!='SPHEREx-D2': continue
        source=str(row['access_url'])
        params={'center':'210.80227,54.34895d','size':0.1}
        url=source+'?'+urlencode(params)
        cached_url=source+'?'+urlencode(dict(params,gzip='false'))
        path=args.fits_dir/(sha256(cached_url.encode()).hexdigest()+'.fits')
        if not path.is_file() or path.stat().st_size>8*1024*1024: continue
        # These are LOCAL FIXTURE identifiers, not products discovered through BE-01.
        identifier=sha256(('offline-fixture:'+cached_url).encode()).hexdigest()
        product=SourceProduct(identifier,urlsplit(source).path.lstrip('/'),url,
                              210.80227,54.34895,.1,'SPHEREx-D2',float(row['t_min']))
        inputs.append((product,path))
        if len(inputs)==args.maximum: break
    if not inputs: raise ValueError('No matching bounded cached FITS')
    request=TimelapseRequest('offline-preview',tuple(p.product_id for p,_ in inputs),210.80227,54.34895,.1)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='be02-real-check-') as folder:
        downloaded=False
        if args.download_one:
            product,_=inputs[0]
            validate_cutout_url(product.cutout_url,product.artifact_uri,request.ra_deg,request.dec_deg,request.size_deg)
            destination=Path(folder)/'fresh.fits'
            IrsaCutoutDownloader(timeout_seconds=30,retries=1).download(product.cutout_url,destination,8*1024*1024)
            inputs[0]=(product,destination)
            downloaded=True
        frames,provenance=FitsSequenceRenderer().render(inputs,request,args.output_dir)
    for metadata in provenance['observations']:
        metadata['sia_t_min_mjd']=metadata.pop('tap_time_bounds_lower')
        metadata.pop('tap_time_interpretation')
        metadata.pop('source_object_key')
        metadata['reference_note']='Offline fixture from local SIA, not resolved by BE-01/TAP'
    # Object keys are renderer proposals only: no storage API was invoked.
    evidence={'mode':'offline_cached_real_fits_preview','end_to_end_accepted':False,
              'observation_service_resolved':False,'storage_verified':False,
              'irsa_cutout_downloaded_this_run':downloaded,'frame_count':len(frames),
              'frames':frames,'provenance':provenance,
              'note':'Local fixture IDs and proposed keys are not an accepted Jobs manifest or uploaded objects.'}
    (args.output_dir/'validation.json').write_text(json.dumps(evidence,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'mode':evidence['mode'],'frames':len(frames),'fresh_irsa_download':downloaded,
                      'timestamps':[f['timestamp'] for f in frames],'end_to_end_accepted':False},indent=2))


if __name__=='__main__': main()
