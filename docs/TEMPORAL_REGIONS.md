# Temporal discovery regional diagnosis (2026-11-15)

Status: **real multi-region acceptance blocked**, not passed. See
[evidence](evidence/temporal_regions/validation.json). No FITS downloaded.

## Observed root cause

Current `fix/temporal-discovery` had numerous pre-existing uncommitted MVP changes;
those were retained. All changes in this investigation are restricted to Observation
HTTP diagnostics, its HTTP tests, a bounded metadata audit script and this evidence.

The running Observation logs show native TAP `DALServiceError`, HTTP 502, then
SIA `requests.HTTPError`, HTTP 502, after one retry. The failure is in
`services/observation/infrastructure/temporal.py:IrsaSiaTemporalCatalog.search`
when `_get_collection` performs the upstream GET, before deduplication, ordering,
persistence or result caching. Direct requests with `trust_env=False` also fail.
The returned HTML says `Proxy Error / Error reading from remote server`.
This identifies an upstream gateway/transport failure, not proof of a specific
internal IRSA component outage. Valid coordinates are not the demonstrated cause.

M101 (210.80227,54.34895), the supplied SIA reference
(304.693508808,42.4436872991), and the ICRS north pole (0,90) were probed in
QR3, QR3 Deep, DR1 and DR1 Deep with point-containment geometry: all twelve
upstream requests returned 502. The public API returned 502 for all three regions
(size .1, D2, limit 2). Supplemental DR1 requests with a nonzero one-arcsecond
radius failed at both M101 and the supplied reference too. Thus zero radius is
not the demonstrated cause. No detector coverage or empty sky can be established
from these failures. The pole is a coordinate-boundary probe, not a claim of no coverage.

Previous successful M101 evidence describes a different run; it does not establish
present connectivity. Exact-query cache keys include geometry, detector and limit,
so success of an older cached query would not prove a fresh region works. This
investigation did not prove that caching caused the historical difference.

## Correction and unchanged scientific contract

`TemporalSearchView.get` previously returned `IRSA TAP query unavailable` for
*any* temporal failure, including SIA. It now returns the transport-neutral
`IRSA temporal discovery unavailable` (same 502 and detail envelope), logging
exception type, upstream HTTP status, region and detector. No upstream exception
is converted into an empty success; failures do not populate result snapshots.
No credentials or remote response body are logged by the new diagnostic.

The discovery strategy remains native TAP with a 10-second read timeout, followed
by bounded SIA only on `TapUnavailable`. Native TAP remains separately callable.
A TAP query/metadata defect is not silently bypassed. SIA queries all four
collections, retries transient failures once, rejects overflow/invalid metadata,
filters the requested detector, deduplicates products, sorts by MJD lower bound,
and returns the chronological prefix. A failed collection prevents a misleading
partial result. Successful empty discovery is HTTP 200 with count 0 and results [].
No global TAP circuit breaker or SIA-first policy was introduced: both protocols
currently fail, and bypassing TAP would not repair the demonstrated outage or
justify changing archive coverage semantics. Native TAP success is not claimed.

Input validation precedes discovery (live invalid RA, Dec and size each return
400). Repository/cache failures retain 503; upstream failures retain 502.
Redis is an optional hot query cache with PostgreSQL snapshot fallback;
rate limiting separately requires Redis. Product detail uses stored metadata,
not another IRSA query. IDs, cutout URLs, detector, MJD timestamps and the
BE-02 SourceProduct / BE-03 product reference contracts remain unchanged.

## Commands and verification

Run from the backend repository in Windows PowerShell:

```powershell
.venv/Scripts/python.exe -X utf8 -B -m scripts.validate_temporal_regions
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider -m 'not integration' --ignore=tests/test_spectral_contract.py --basetemp=.pytest_cache/temporal-regions-final-20261115
.venv/Scripts/python.exe -B tests/smoke_stdlib.py
git diff --check -- services/observation/presentation/views.py tests/test_observation_http.py
```

170 mocked/unit regressions passed, six live integration tests deselected; five
stdlib smoke checks passed. New HTTP regressions cover M101, the supplied reference
and the pole, TAP timeout/SIA success, empty result caching, both upstreams failing,
and no failure caching. Existing tests cover detector filtering, chronological
ordering, product IDs, BE-02 resolution, malformed scientific metadata and invalid
inputs. These tests use mocks and do not establish real archive coverage.

Initial focused run: 86 passed, one Windows temporary-directory permissions error;
using a dedicated basetemp resolved that infrastructure problem. Full collection
on Windows is blocked by Spectral's Linux-only `fcntl`; an isolated read-only,
network-disabled run of the current Observation image was attempted but that image
has no pytest. Spectral/application algorithms were not changed.

Running services were not rebuilt or restarted. Live public HTTP evidence therefore
retains the old error string; the corrected response and logging were tested using
DRF APIClient against current source. No success or successful empty region was
observed against IRSA. Acceptance requires repeating the bounded audit when IRSA
responds, identifying available detectors, obtaining genuine references in at least
two regions, and resolving their detail endpoints. No commit, push, branch switch,
configuration-secret edits, volume changes or FITS processing occurred.
