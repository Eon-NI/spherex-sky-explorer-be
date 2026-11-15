"""Acquisition guards use bounded fixtures and never open sockets."""
import io
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from services.image_processing.domain.security import validate_cutout_url
from services.image_processing.infrastructure.downloader import IrsaCutoutDownloader, _NoRedirect

ARTIFACT = "ibe/data/spherex/dr1/level2/x/a.fits"
URL = "https://irsa.ipac.caltech.edu/" + ARTIFACT + "?center=1,2d&size=0.1"


@pytest.mark.parametrize("url", [URL.replace('https:', 'http:'), URL.replace('center=1,2d','center=nan,2d'), URL.replace('irsa.ipac.caltech.edu','evil.example'),
    URL.replace('/level2/x/','/level2/../'),URL.replace('size=0.1','size=0.1&size=0.2'),
    URL.replace('size=0.1','size=0.1&extra=1'),URL.replace('irsa.ipac.caltech.edu','irsa.ipac.caltech.edu:8000')])
def test_unauthorized_source(url):
    with pytest.raises(ValueError): validate_cutout_url(url,ARTIFACT,1,2,.1)


def test_redirect_handler_never_constructs_followup_request():
    assert _NoRedirect().redirect_request(None,None,302,'moved',{},'https://evil.example/') is None


def test_downloader_redirect_error_not_followed(tmp_path):
    downloader=IrsaCutoutDownloader(timeout_seconds=2,retries=1)
    downloader.opener=Mock()
    downloader.opener.open.side_effect=HTTPError(URL,302,'moved',{'Location':'https://evil.example/'},None)
    with pytest.raises(HTTPError): downloader.download(URL,tmp_path/'out.fits',5000)
    downloader.opener.open.assert_called_once()
    assert downloader.opener.open.call_args.kwargs['timeout'] == 2


def test_oversized_stream_is_bounded(tmp_path):
    class Response(io.BytesIO):
        status=200
        @property
        def headers(self): return {}
    downloader=IrsaCutoutDownloader(retries=1)
    downloader.opener=Mock()
    downloader.opener.open.return_value=Response(b'SIMPLE  ='+b' '*6000)
    destination=tmp_path/'out.fits'
    with pytest.raises(ValueError,match='size limit'): downloader.download(URL,destination,3000)
    assert not destination.exists() or destination.stat().st_size <= 3000
