"""Focused regressions: proxy contamination and TAP transport failure -> real metadata DTO."""
from unittest.mock import Mock, patch

import astropy.units as u
import pytest
import requests
from astropy.table import Table

from services.image_processing.domain.models import SourceProduct
from services.observation.domain.models import COLLECTIONS, TemporalObservation, TemporalSearch
from services.observation.infrastructure.tap import IrsaTapCatalog, TapSession, TapUnavailable
from services.observation.infrastructure.temporal import IrsaSiaTemporalCatalog, IrsaTemporalCatalog
from services.observation.presentation.views import TemporalObservationSerializer

QUERY = TemporalSearch(210.80227,54.34895,.1,'SPHEREx-D2',5)


def test_proxy_environment_does_not_poison_public_tap(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY','http://unavailable-proxy.invalid:9')
    monkeypatch.delenv('IRSA_TAP_TRUST_ENV',raising=False)
    with patch('requests.adapters.HTTPAdapter.send',side_effect=requests.ReadTimeout('simulated')) as send, pytest.raises(TapUnavailable):
        IrsaTapCatalog(read_timeout=3).search(QUERY)
    assert send.call_args.kwargs['proxies']=={}
    assert send.call_args.kwargs['timeout']==(15,3)
    assert TapSession(3).trust_env is False
    monkeypatch.setenv('IRSA_TAP_TRUST_ENV','true')
    assert TapSession(3).trust_env is True


def test_transport_failure_uses_fallback_without_schema_changes():
    primary=Mock();primary.search.side_effect=TapUnavailable('read timeout')
    fallback=Mock();fallback.search.return_value=[]
    assert IrsaTemporalCatalog(primary,fallback).search(QUERY)==[]
    fallback.search.assert_called_once_with(QUERY)


def test_invalid_tap_metadata_never_silently_falls_back():
    primary=Mock();primary.search.side_effect=ValueError('invalid scientific reference')
    fallback=Mock()
    with pytest.raises(ValueError): IrsaTemporalCatalog(primary,fallback).search(QUERY)
    fallback.search.assert_not_called()


def table(collection):
    # Real protocol-shaped rows; each wide/deep pair repeats the same artifact.
    release='qr3' if 'qr3' in collection else 'dr1'
    rows=[]
    for name,time,band in [('b',60806.,'SPHEREx-D2'),('a',60805.,'SPHEREx-D2'),('c',60804.,'SPHEREx-D1')]:
        rows.append((f'https://irsa.ipac.caltech.edu/ibe/data/spherex/{release}/level2/test/{name}.fits',
                     band,time,time+.001,collection))
    t=Table(rows=rows,names=['access_url','energy_bandpassname','t_min','t_max','obs_collection'])
    t['t_min'].unit=u.day;t['t_max'].unit=u.day
    return t


def test_sia_point_time_selection_dedup_and_be02_contract():
    with patch('services.observation.infrastructure.temporal._get_collection',side_effect=lambda c,q,**kw:table(c)) as get:
        rows=IrsaSiaTemporalCatalog().search(QUERY)
    assert get.call_count==4
    assert [call.args[0] for call in get.call_args_list]==list(COLLECTIONS)
    assert all(call.kwargs['radius_deg']==0 for call in get.call_args_list)
    assert all(call.kwargs['session'].trust_env is False for call in get.call_args_list)
    assert len(rows)==4 and len({r.product_id for r in rows})==4
    assert [r.time_bounds_lower for r in rows]==[60805.,60805.,60806.,60806.]
    for row in rows:
        data=dict(TemporalObservationSerializer(row).data)
        assert SourceProduct(**data).product_id==row.product_id
        assert row.product_id==TemporalObservation.product_key(row.artifact_uri,QUERY.ra_deg,QUERY.dec_deg,QUERY.size_deg,QUERY.detector,row.time_bounds_lower)


@pytest.mark.parametrize('mutation',['missing_time','inverted_time','untrusted_url'])
def test_invalid_sia_fallback_fails_closed(mutation):
    t=table('spherex_qr3')
    if mutation=='missing_time':t.remove_column('t_min')
    if mutation=='inverted_time':t['t_max'][0]=60000
    if mutation=='untrusted_url':t['access_url'][0]='https://evil.example/a.fits'
    with patch('services.observation.infrastructure.temporal._get_collection',return_value=t),pytest.raises(ValueError):
        IrsaSiaTemporalCatalog().search(QUERY)


def test_sia_transient_proxy_502_retries_once(monkeypatch):
    monkeypatch.setattr('services.observation.infrastructure.temporal.sleep',lambda seconds:None)
    response=requests.Response();response.status_code=502
    error=requests.HTTPError('proxy error',response=response)
    calls=[]
    def fetch(collection,query,**kwargs):
        calls.append(collection)
        if len(calls)==1:raise error
        return table(collection)
    with patch('services.observation.infrastructure.temporal._get_collection',side_effect=fetch):
        assert len(IrsaSiaTemporalCatalog().search(QUERY))==4
    assert len(calls)==5


def test_sia_persistent_502_fails_without_partial_catalog(monkeypatch):
    monkeypatch.setattr('services.observation.infrastructure.temporal.sleep',lambda seconds:None)
    response=requests.Response();response.status_code=502
    with patch('services.observation.infrastructure.temporal._get_collection',side_effect=requests.HTTPError('proxy error',response=response)) as fetch,pytest.raises(requests.HTTPError):
        IrsaSiaTemporalCatalog().search(QUERY)
    assert fetch.call_count==2


def test_tap_query_error_is_not_masked_by_sia_even_with_http_500():
    from pyvo.dal.exceptions import DALQueryError
    with patch('services.observation.infrastructure.tap.TapSession') as session, patch('services.observation.infrastructure.tap.IrsaClass.query_tap',side_effect=DALQueryError('invalid ADQL')):
        session.return_value.__enter__.return_value.last_status=500
        fallback=Mock()
        with pytest.raises(RuntimeError) as error:
            IrsaTemporalCatalog(primary=IrsaTapCatalog(),fallback=fallback).search(QUERY)
        assert not isinstance(error.value,TapUnavailable)
        fallback.search.assert_not_called()


def test_failed_transport_is_closed_before_retry(monkeypatch):
    import requests
    from astropy.table import Table

    from services.observation.domain.models import TemporalSearch
    from services.observation.infrastructure import temporal

    sessions = []
    class Session:
        last_status = 502
        transport_error = None
        closed = False
        def __init__(self, *_):
            sessions.append(self)
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.closed = True

    attempts = []
    def get_collection(collection, query, *, session, radius_deg):
        assert all(s.closed for s in sessions if s is not session)
        attempts.append(collection)
        if len(attempts) == 1:
            response = requests.Response()
            response.status_code = 502
            raise requests.HTTPError(response=response)
        return Table(names=['access_url', 'energy_bandpassname', 't_min', 't_max', 'obs_collection'],
                     dtype=['U1', 'U1', float, float, 'U1'])
    monkeypatch.setattr(temporal, 'TapSession', Session)
    monkeypatch.setattr(temporal, '_get_collection', get_collection)
    monkeypatch.setattr(temporal, 'sleep', lambda _: None)
    assert temporal.IrsaSiaTemporalCatalog().search(TemporalSearch(1, 2, .1, 'SPHEREx-D2', 5)) == []
    assert all(s.closed for s in sessions)
    assert len(sessions) == 5
