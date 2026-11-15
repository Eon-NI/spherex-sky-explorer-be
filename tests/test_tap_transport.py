"""Exercise actual Astroquery/PyVO -> Requests boundary without opening a socket."""
from unittest.mock import patch

import pytest
import requests

from services.observation.domain.models import TemporalSearch
from services.observation.infrastructure.tap import IrsaTapCatalog

QUERY = TemporalSearch(210.80227, 54.34895, 0.1, "SPHEREx-D2", 5)


@pytest.mark.parametrize("timeout,expected", [("7", 7), ("999", 120), ("0", 1)])
def test_timeout_reaches_real_http_transport(monkeypatch, timeout, expected):
    monkeypatch.setenv("IRSA_TIMEOUT_SECONDS", timeout)
    with patch('requests.adapters.HTTPAdapter.send', side_effect=requests.Timeout('simulated')) as send, pytest.raises(RuntimeError, match='IRSA TAP query unavailable'):
        IrsaTapCatalog().search(QUERY)
    assert send.call_count == 1
    assert send.call_args.kwargs["timeout"] == (15, expected)
    assert send.call_args.args[0].url == "https://irsa.ipac.caltech.edu/TAP/sync"


def test_connection_error_is_controlled():
    with patch('requests.adapters.HTTPAdapter.send', side_effect=requests.ConnectionError('offline')), pytest.raises(RuntimeError, match='unavailable'):
        IrsaTapCatalog().search(QUERY)


def row(name, timestamp):
    artifact = f"ibe/data/spherex/dr1/level2/test/{name}.fits"
    return {"artifact_uri": artifact, "uri": "https://irsa.ipac.caltech.edu/" + artifact +
            "?center=210.80227,54.34895d&size=0.1", "time_bounds_lower": timestamp}


def test_tap_tie_order_deduplication_and_stable_keys():
    rows = [row("z", 2), row("b", 1), row("a", 1), row("a", 1)]
    with patch("services.observation.infrastructure.tap.IrsaClass.query_tap", return_value=rows):
        first = IrsaTapCatalog().search(QUERY)
    with patch("services.observation.infrastructure.tap.IrsaClass.query_tap", return_value=list(reversed(rows))):
        second = IrsaTapCatalog().search(QUERY)
    assert first == second and len(first) == 3
    assert [p.time_bounds_lower for p in first] == [1, 1, 2]
    assert first[0].artifact_uri.endswith("a.fits")
    assert "ORDER BY p.time_bounds_lower, a.uri" in IrsaTapCatalog.build_adql(QUERY)


@pytest.mark.parametrize("change", [
    {"uri": "https://evil.example/a.fits"},
    {"time_bounds_lower": float("nan")},
    {"artifact_uri": "ibe/data/spherex/dr1/level2/test/other.fits"},
    {"uri": row("a", 1)["uri"].replace("size=0.1", "size=0.2")},
])
def test_tap_rejects_invalid_scientific_references(change):
    with patch('services.observation.infrastructure.tap.IrsaClass.query_tap', return_value=[dict(row('a', 1), **change)]), pytest.raises(RuntimeError, match='unavailable'):
        IrsaTapCatalog().search(QUERY)
