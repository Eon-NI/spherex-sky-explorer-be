"""Official DR1 HiPS cube adapter. URLs are fixed here, never supplied by callers."""
import json
import os
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from typing import Callable

import boto3
import hpgeom
import requests
from django.conf import settings
from services.spectral.domain.models import BLANK_DICHROIC_CHANNELS, DR1_HIPS_PRODUCT_ID

HIPS_ROOT = "https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube"
DATA_ROOT = "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/hips5_cube/hips_20261026"


def tile_number(ra_deg: float, dec_deg: float) -> int:
    return int(hpgeom.angle_to_pixel(32, ra_deg, dec_deg, nest=True, lonlat=True))


def tile_url(tile: int) -> str:
    if not 0 <= tile < 12288:
        raise ValueError("Invalid order-5 tile")
    directory = (tile // 10000) * 10000
    return f"{DATA_ROOT}/Norder5/Dir{directory}/Npix{tile}_cube.fits"


def channel_metadata() -> dict:
    response = requests.get(f"{HIPS_ROOT}/properties", timeout=(10, 20), allow_redirects=False)
    response.raise_for_status()
    if len(response.content) > 100_000:
        raise ValueError("HiPS properties too large")
    properties = {}
    for line in response.text.splitlines():
        if not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            properties[key.strip()] = value.strip()
    if (properties.get("creator_did") != DR1_HIPS_PRODUCT_ID or
            properties.get("hips_cube_depth") != "102" or
            properties.get("data_cube_bunit3") != "um"):
        raise ValueError("Unexpected DR1 HiPS properties")
    wavelengths = [float(value) for value in properties["data_cube_crval3_arr"].split()]
    if len(wavelengths) != 102 or any(not 0.7 < value < 5.1 for value in wavelengths):
        raise ValueError("Invalid channel wavelength table")
    return {"product_id": DR1_HIPS_PRODUCT_ID, "wavelength_unit": "um",
            "channels": [{"channel_index": index, "band": index // 17 + 1,
                          "band_channel": index % 17 + 1, "wavelength_um": value,
                          "available": index not in BLANK_DICHROIC_CHANNELS}
                         for index, value in enumerate(wavelengths)]}


class CubeStore:
    def __init__(self):
        self.bucket = os.environ["S3_BUCKET"]
        self.client = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
                                   aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
                                   aws_secret_access_key=os.environ.get("S3_SECRET_KEY"))

    @staticmethod
    def cube_key(tile: int) -> str:
        return f"spectral/dr1/hips_20261026/Norder5/Npix{tile}_cube.fits"

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def put_json(self, key: str, value: dict) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(value).encode(),
                               ContentType="application/json")

    def get_json(self, key: str) -> dict:
        return json.loads(self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read())

    def _download_parallel(self, tile: int, path: Path,
                           progress: Callable[[int], None] | None) -> bool:
        """Use four bounded HTTP byte ranges when IRSA confirms a stable FITS object."""
        maximum = settings.EON_MAX_SPECTRAL_CUBE_BYTES
        with requests.get(tile_url(tile), headers={"Range": "bytes=0-2879"}, stream=True,
                          timeout=(10, 30), allow_redirects=False) as probe:
            if probe.status_code == 200:
                return False  # Older servers may ignore Range; use the serial path.
            probe.raise_for_status()
            match = re.fullmatch(r"bytes 0-2879/(\d+)", probe.headers.get("Content-Range", ""))
            etag = probe.headers.get("ETag")
            if probe.status_code != 206 or not match or not etag:
                raise ValueError("IRSA did not provide a verifiable FITS byte range")
            expected = int(match.group(1))
            if not 2880 <= expected <= maximum or expected % 2880:
                raise ValueError("Invalid or oversized DR1 HiPS tile length")
            head = b"".join(probe.iter_content(2880))
            if len(head) != 2880 or not head.startswith(b"SIMPLE"):
                raise ValueError("Invalid FITS tile header")

        workers = max(1, min(4, int(getattr(settings, "EON_SPECTRAL_DOWNLOAD_WORKERS", 4))))
        remaining = expected - len(head)
        chunk_size = (remaining + workers - 1) // workers
        ranges = [(start, min(start + chunk_size - 1, expected - 1))
                  for start in range(len(head), expected, chunk_size)] if remaining else []
        lock = threading.Lock()
        received = len(head)
        reported = -1
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.ftruncate(descriptor, expected)
            os.pwrite(descriptor, head, 0)

            def download_range(start: int, end: int) -> None:
                nonlocal received, reported
                position = start
                for attempt in range(5):
                    try:
                        with requests.get(tile_url(tile), headers={
                            "Range": f"bytes={position}-{end}", "If-Range": etag,
                        }, stream=True, timeout=(10, 120), allow_redirects=False) as response:
                            response.raise_for_status()
                            content_range = response.headers.get("Content-Range", "")
                            if (response.status_code != 206 or
                                    content_range != f"bytes {position}-{end}/{expected}" or
                                    response.headers.get("ETag") != etag):
                                raise ValueError("IRSA FITS byte range or ETag changed")
                            for block in response.iter_content(1024 * 1024):
                                if not block:
                                    continue
                                if position + len(block) > end + 1:
                                    raise ValueError("IRSA FITS range exceeded its boundary")
                                written = 0
                                while written < len(block):
                                    written += os.pwrite(descriptor, block[written:], position + written)
                                position += len(block)
                                with lock:
                                    received += len(block)
                        if position == end + 1:
                            return
                        raise requests.ConnectionError("Incomplete IRSA FITS byte range")
                    except requests.RequestException:
                        if attempt == 4:
                            raise
                        time.sleep(min(2 ** attempt, 8))
                raise ValueError("Incomplete IRSA FITS byte range")

            with ThreadPoolExecutor(max_workers=workers) as executor:
                pending = {executor.submit(download_range, start, end) for start, end in ranges}
                while pending:
                    finished, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                    with lock:
                        percent = min(50, int(50 * received / expected))
                    if progress and percent > reported:
                        progress(percent)  # Celery task context exists only on this thread.
                        reported = percent
                    for future in finished:
                        future.result()
            if received != expected:
                raise ValueError("Incomplete DR1 HiPS tile")
            return True
        finally:
            os.close(descriptor)

    def local_cube(self, tile: int, progress: Callable[[int], None] | None = None):
        """Reuse a validated immutable DR1 tile on this process's local disk."""
        cache_limit = int(getattr(settings, "EON_SPECTRAL_CACHE_MAX_BYTES", 700 * 1024 * 1024))
        if cache_limit <= 0:
            return self._local_cube_uncached(tile, progress)
        cache_dir = Path(getattr(settings, "EON_SPECTRAL_CACHE_DIR", "/tmp/eon-spectral-cubes"))
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        cached = cache_dir / f"Npix{tile}_cube.fits"
        lock_path = cache_dir / f"Npix{tile}.lock"
        with lock_path.open("a+b") as lock_file:
            flock(lock_file.fileno(), LOCK_EX)
            try:
                if cached.exists():
                    size = cached.stat().st_size
                    if 2880 <= size <= settings.EON_MAX_SPECTRAL_CUBE_BYTES:
                        with cached.open("rb") as existing:
                            valid = existing.read(6) == b"SIMPLE"
                        if valid:
                            directory = tempfile.TemporaryDirectory()
                            path = Path(directory.name) / "cube.fits"
                            try:
                                os.link(cached, path)
                            except OSError:
                                shutil.copyfile(cached, path)
                            os.utime(cached, None)
                            if progress:
                                progress(50)
                            return directory, path
                    cached.unlink()

                directory, path = self._local_cube_uncached(tile, progress)
                size = path.stat().st_size
                if size <= cache_limit:
                    entries = sorted(cache_dir.glob("Npix*_cube.fits"), key=lambda entry: entry.stat().st_mtime)
                    used = sum(entry.stat().st_size for entry in entries)
                    for entry in entries:
                        if used + size <= cache_limit:
                            break
                        used -= entry.stat().st_size
                        entry.unlink()
                    staging = cache_dir / f".{tile}.{os.getpid()}.tmp"
                    try:
                        try:
                            os.link(path, staging)
                        except OSError:
                            shutil.copyfile(path, staging)
                        os.replace(staging, cached)
                    finally:
                        staging.unlink(missing_ok=True)
                return directory, path
            finally:
                flock(lock_file.fileno(), LOCK_UN)

    def _local_cube_uncached(self, tile: int, progress: Callable[[int], None] | None = None):
        """Caller owns returned temporary directory and file."""
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "cube.fits"
        key = self.cube_key(tile)
        try:
            if self.exists(key):
                size = self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
                if size > settings.EON_MAX_SPECTRAL_CUBE_BYTES:
                    raise ValueError("Stored DR1 HiPS tile exceeds configured byte cap")
                self.client.download_file(self.bucket, key, str(path))
                if progress:
                    progress(50)
                return directory, path

            if self._download_parallel(tile, path, progress):
                self.client.upload_file(str(path), self.bucket, key,
                                        ExtraArgs={"ContentType": "application/fits"})
                return directory, path

            maximum = settings.EON_MAX_SPECTRAL_CUBE_BYTES
            expected = None
            length = 0
            last_reported = -1
            for attempt in range(10):
                headers = {"Range": f"bytes={length}-"} if length else {}
                try:
                    with requests.get(tile_url(tile), headers=headers, stream=True,
                                      timeout=(10, 120), allow_redirects=False) as response:
                        response.raise_for_status()
                        if response.is_redirect:
                            raise ValueError("Redirected IRSA tile")
                        if length:
                            content_range = response.headers.get("Content-Range", "")
                            if (response.status_code != 206 or expected is None or
                                    not re.fullmatch(rf"bytes {length}-\d+/{expected}", content_range)):
                                raise ValueError("IRSA did not honor FITS byte range")
                        else:
                            if response.status_code != 200:
                                raise ValueError("Unexpected IRSA FITS status")
                            expected = int(response.headers.get("Content-Length", "0"))
                            if not 2880 <= expected <= maximum:
                                raise ValueError("Invalid or oversized DR1 HiPS tile length")
                        with path.open("ab") as output:
                            for chunk in response.iter_content(1024 * 1024):
                                length += len(chunk)
                                if length > expected:
                                    raise ValueError("DR1 HiPS tile exceeds declared length")
                                output.write(chunk)
                                percent = min(50, int(50 * length / expected))
                                if progress and percent > last_reported:
                                    progress(percent)
                                    last_reported = percent
                except requests.RequestException:
                    if length == expected:
                        break
                    if attempt == 9:
                        raise
                    time.sleep(min(2 ** attempt, 10))
                    continue
                if length == expected:
                    break
            if length != expected:
                raise ValueError("Incomplete DR1 HiPS tile")
            with path.open("rb") as downloaded:
                signature = downloaded.read(6)
            if signature != b"SIMPLE":
                raise ValueError("Invalid FITS tile")
            self.client.upload_file(str(path), self.bucket, key,
                                    ExtraArgs={"ContentType": "application/fits"})
            return directory, path
        except Exception:
            directory.cleanup()
            raise
