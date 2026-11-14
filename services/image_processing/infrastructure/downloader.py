"""Bounded streaming downloader with redirects disabled."""
from pathlib import Path
import http.client
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class IrsaCutoutDownloader:
    def __init__(self, timeout_seconds: int = 45, retries: int = 3):
        self.timeout_seconds, self.retries = timeout_seconds, retries
        self.opener = build_opener(_NoRedirect)

    def download(self, url: str, destination: Path, max_bytes: int) -> int:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        for attempt in range(self.retries):
            try:
                with self.opener.open(Request(url, headers={"Accept": "application/fits"}),
                                      timeout=self.timeout_seconds) as response:
                    if response.status != 200:
                        raise RuntimeError(f"IRSA returned HTTP {response.status}")
                    length = response.headers.get("Content-Length")
                    if length and int(length) > max_bytes:
                        raise ValueError("Cutout exceeds size limit")
                    count = 0
                    with destination.open("wb") as target:
                        while chunk := response.read(1024 * 1024):
                            count += len(chunk)
                            if count > max_bytes:
                                raise ValueError("Cutout exceeds size limit")
                            target.write(chunk)
                    if count < 2880:
                        raise ValueError("Cutout is too small for FITS")
                    with destination.open("rb") as source:
                        if not source.read(80).startswith(b"SIMPLE  ="):
                            raise ValueError("IRSA response is not FITS")
                    return count
            except (HTTPError, URLError, TimeoutError, OSError,
                    http.client.IncompleteRead, http.client.RemoteDisconnected):
                destination.unlink(missing_ok=True)
                if attempt == self.retries - 1:
                    raise
                sleep(2 ** attempt)
        raise RuntimeError("Download failed")
