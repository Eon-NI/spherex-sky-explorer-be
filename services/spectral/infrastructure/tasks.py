"""Celery tasks keep FITS bytes in bounded files and S3, never in messages."""
from astropy.io import fits
from celery import shared_task

from services.spectral.application.use_cases import channels_from_fits, cube_metadata, extract_spectrum
from services.spectral.infrastructure.hips import CubeStore, tile_number, tile_url


@shared_task(name="eon.spectral.prepare_cube", bind=True, time_limit=1800,
             acks_late=True, reject_on_worker_lost=True)
def prepare_cube(self, ra_deg: float, dec_deg: float, fov_deg: float) -> dict:
    tile = tile_number(ra_deg, dec_deg)
    store = CubeStore()
    directory, path = store.local_cube(
        tile, lambda percent: self.update_state(state="PROGRESS", meta={"progress": percent}))
    try:
        self.update_state(state="PROGRESS", meta={"progress": 60})
        with fits.open(path, memmap=True) as hdul:
            metadata = cube_metadata(hdul, tile, ra_deg, dec_deg, fov_deg,
                                     channels_from_fits(hdul))
        metadata["source_url"] = tile_url(tile)
        from hashlib import sha256
        digest = sha256(f"{tile}:{ra_deg:.10f}:{dec_deg:.10f}:{fov_deg:.10f}".encode()).hexdigest()
        key = f"spectral/dr1/metadata/{digest}.json"
        store.put_json(key, metadata)
        return {**metadata, "object_key": store.cube_key(tile), "metadata_key": key}
    finally:
        directory.cleanup()


@shared_task(name="eon.spectral.extract_spectrum", bind=True, time_limit=1800,
             acks_late=True, reject_on_worker_lost=True)
def extract_spectrum_task(self, ra_deg: float, dec_deg: float, fov_deg: float) -> dict:
    tile = tile_number(ra_deg, dec_deg)
    store = CubeStore()
    directory, path = store.local_cube(
        tile, lambda percent: self.update_state(state="PROGRESS", meta={"progress": percent}))
    try:
        self.update_state(state="PROGRESS", meta={"progress": 70})
        with fits.open(path, memmap=True) as hdul:
            spectrum = extract_spectrum(hdul, tile, ra_deg, dec_deg,
                                        channels_from_fits(hdul))
        spectrum["source_url"] = tile_url(tile)
        from hashlib import sha256
        digest = sha256(f"{tile}:{ra_deg:.10f}:{dec_deg:.10f}".encode()).hexdigest()
        key = f"spectral/dr1/spectra/{digest}.json"
        store.put_json(key, spectrum)
        return {**spectrum, "object_key": key}
    finally:
        directory.cleanup()
