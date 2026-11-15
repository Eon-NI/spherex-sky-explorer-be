"""Audit retained real BE-02 objects and run the existing opt-in integration test."""
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from PIL import Image

from services.image_processing.domain.models import ImageReference
from services.image_processing.domain.timelapse import TimelapseRequest
from services.image_processing.infrastructure.clients import ObservationApiResolver
from services.image_processing.infrastructure.storage import S3ScientificObjectStore
from services.image_processing.infrastructure.timelapse import read_frame, target_grid


def main():
    root = Path('docs/evidence/BE02_revalidation')
    for line in Path('.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        if key.strip() in {'S3_ACCESS_KEY', 'S3_SECRET_KEY'}:
            os.environ.setdefault(key.strip(), value.strip().strip(chr(34)).strip(chr(39)))
    os.environ.update(S3_ENDPOINT_URL='http://127.0.0.1:9000', S3_BUCKET='eon-science',
                      OBSERVATION_SERVICE_URL='http://127.0.0.1:8001',
                      NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
    request = TimelapseRequest(**json.loads((root / 'request.json').read_text()))
    manifest = json.loads((root / 'manifest.json').read_text())
    store = S3ScientificObjectStore()
    resolver = ObservationApiResolver()
    grid, shape = target_grid(request)
    assert shape == tuple(manifest['provenance']['grid']['shape'])
    assert grid.wcs.cdelt[0] < 0 < grid.wcs.cdelt[1]
    frames, checks = [], []
    with tempfile.TemporaryDirectory(prefix='be02-object-audit-') as temp:
        directory = Path(temp)
        for frame, meta in zip(manifest['frames'], manifest['provenance']['observations'], strict=True):
            product = resolver.get(frame['product_id'])
            source = directory / (frame['product_id'] + '.fits')
            store.download_file(ImageReference(meta['source_object_key']), source)
            assert sha256(source.read_bytes()).hexdigest() == meta['source_sha256']
            with fits.open(source) as hdul:
                hdul.verify('exception')
                spatial = WCS(hdul['IMAGE'].header, fobj=hdul).celestial
                yy, xx = np.indices(shape)
                sky = grid.pixel_to_world(xx, yy)
                sx, sy = spatial.world_to_pixel(sky)
                back = spatial.pixel_to_world(sx, sy)
                error = float(np.max(sky.separation(back).arcsec))
                assert error < 0.01
            aligned = read_frame(product, source, request, grid, shape)
            assert aligned['metadata']['timestamp'] == frame['timestamp']
            delta = abs(aligned['metadata']['mjd_utc'] - product.time_bounds_lower) * 86400
            assert delta < 1
            frames.append(aligned)
            checks.append({'product_id': frame['product_id'], 'timestamp': frame['timestamp'],
                           'source_sha256': meta['source_sha256'], 'source_bytes': source.stat().st_size,
                           'wcs_roundtrip_max_arcsec': error, 'catalog_fits_time_difference_seconds': delta,
                           'coverage_fraction': aligned['metadata']['coverage_fraction']})
        values = np.concatenate([f['image'][np.isfinite(f['image'])] for f in frames])
        low, high = np.percentile(values, [1, 99.5])
        normalization = manifest['provenance']['normalization']
        assert np.allclose([low, high], [normalization['vmin'], normalization['vmax']], rtol=0, atol=1e-12)
        for aligned, frame, check in zip(frames, manifest['frames'], checks, strict=True):
            data = aligned['image']
            finite = np.isfinite(data)
            normalized = np.clip((np.where(finite, data, low) - low) / (high - low), 0, 1)
            gray = np.rint(255 * np.arcsinh(normalized / 0.1) / np.arcsinh(10)).astype(np.uint8)
            expected = np.repeat(gray[..., None], 3, axis=2)
            expected[~finite] = [55, 70, 85]
            expected = np.asarray(Image.fromarray(expected[::-1]).resize((512, 512), Image.Resampling.NEAREST))
            path = directory / f"frame-{frame['index']:03d}.png"
            store.download_file(ImageReference(frame['object_key'], 'image/png'), path)
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                assert image.format == 'PNG' and image.mode == 'RGB' and image.size == (512, 560)
                assert np.array_equal(np.asarray(image)[:512], expected)
            head = store.client.head_object(Bucket=store.bucket, Key=frame['object_key'])
            assert head['ContentType'] == 'image/png' and head['ContentLength'] == path.stat().st_size
            check.update(png_sha256=sha256(path.read_bytes()).hexdigest(), png_bytes=path.stat().st_size,
                         object_key=frame['object_key'], shared_scale_pixels_exact=True)
            (root / path.name).write_bytes(path.read_bytes())
    result = {'checked_at_utc': datetime.now(UTC).isoformat(), 'status': 'passed',
              'grid_shape': list(shape), 'normalization': normalization, 'objects': checks,
              'note': 'WCS roundtrip and exact common-scale pixel checks; no astrometric source-fit or calibrated photometry claim.'}
    (root / 'object_audit.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('Real source FITS, WCS roundtrips and shared-scale PNG pixel audit: passed', flush=True)
    os.environ.update(EON_RUN_BE02_LIVE='1', EON_BE02_REQUEST_FILE=str(root / 'request.json'))
    command = [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               '--basetemp', str(Path(tempfile.gettempdir()) / 'eon-be02-live-revalidation'),
               'tests/integration/test_timelapse_live.py']
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    (root / 'live_pytest.txt').write_text(completed.stdout + completed.stderr, encoding='utf-8')
    print(completed.stdout, flush=True)
    return completed.returncode


if __name__ == '__main__':
    raise SystemExit(main())
