# IRSA temporal discovery: TAP transport diagnosis and bounded SIA2 fallback

## Result and branch isolation

Implemented on **fix/EON-BE-01-tap-discovery**, starting from clean HEAD 87c048d.
No switch, commit, merge, push, .env/Compose change, Jobs change or FITS processing
change was performed. Observation changes are isolated from BE-02 implementation.

The immediate criterion passed against real services:

**IRSA discovery -> temporal product IDs -> PostgreSQL persistence through Observation
HTTP -> temporal detail -> unchanged BE-02 ObservationApiResolver/SourceProduct.**

This succeeded through the explicitly logged SIA2 point fallback. **Native SPHEREx
TAP discovery did not pass. No complete BE-02 FITS/PNG/S3 run is claimed here.**

## Actual failure and limits of the diagnosis

The request path was TemporalSearchView -> SearchTemporalObservations -> exact-query
cache -> IrsaTapCatalog.search -> IrsaClass.query_tap -> PyVO TAPService.run_sync ->
synchronous POST https://irsa.ipac.caltech.edu/TAP/sync.

The old generic HTTP 502 hid two observed transport failures:

1. Environment-inherited proxy requests returned an HTML HTTP 502 Proxy Error even
   for SELECT TOP 1 table_name FROM TAP_SCHEMA.tables. Direct trust_env=False GET/POST
   schema probes returned HTTP 200 in about one second.
2. The full existing SPHEREx query still exhausted a 60-second socket-read timeout
   in a direct Astroquery/PyVO session (about 61.6 s elapsed). The exception chain was
   RuntimeError -> DALFormatError -> Requests ReadTimeout -> urllib3 ReadTimeoutError.
   Requests was waiting for an HTTP response, before VOTable parsing completed.
   Simple TOP 1 reads of spherex.plane and spherex.artifact also timed out with no
   spatial predicate or JOIN. Therefore JOIN/sort cost is not a sufficient explanation.

The API mapped those failures to `{"detail":"IRSA TAP query unavailable"}`. The
client evidence does NOT identify an IRSA-internal database fault, SQL execution
plan or precise outage cause. No IRSA server logs were available. A valid schema,
valid ADQL and TOP 1 do not prove that a SPHEREx data query will complete promptly.
TOP bounds returned rows; it does not guarantee cheap filtering/joining/sorting.

New safe structured logs record endpoint, sync mode, exception type, HTTP status
when available and transport-error type. They never print proxy URLs, credentials,
headers or arbitrary exception text. SIA failures identify the collection and type.
See [diagnostics.json](evidence/TAP_discovery_fix/diagnostics.json) for recorded probes.

## PoC comparison

The accessible PoC is ../SPHERExProofOfConcept/spherex_poc/archive.py and its
README/evidence/real_validation.json. It uses Requests GET to /SIA, Astropy VOTable
parsing with use_names_over_ids=True, QUERY_STATUS/error checks, t_min/t_max day-unit
checks, detector filtering, deduplication, and URLs returned by the service. Its
HTTP timeout is (15,120). It uses no credentials for public IRSA. Its success record
identifies SIA and a local SIA XML; it is not evidence of working native TAP.

Git history also contains the older backend TAP adapter and official tutorial-style
ADQL. No working native TAP PoC run was found. Reusable parts are SIA discovery,
scientific metadata validation and cutout-reference construction, not an alleged
TAP fix. Existing FITS/WCS/processing code was not copied or rewritten.

## Official verification and options considered

- [IRSA TAP instructions](https://irsa.ipac.caltech.edu/docs/program_interface/TAP.html)
  document /TAP/sync, GET, synchronous/asynchronous execution and table/column discovery.
  Sync has a server limit of five minutes; async can run much longer. Those are server
  limits, not permission to wait unboundedly in this API.
- [Official SPHEREx cutout tutorial](https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts/)
  uses spherex.artifact JOIN spherex.plane, a.uri, p.poly, p.energy_bandpassname and
  p.time_bounds_lower, matching the existing adapter's science query.
- Live TAP_SCHEMA.columns confirmed planeid, uri, energy_bandpassname, poly and
  time_bounds_lower. The latter has unit d and description Modified Julian Day;
  poly is an IRSA spatial-index descriptor in degrees.
- [IRSA SIA2](https://irsa.ipac.caltech.edu/ibe/sia.html) specifies intersection with
  the query shape, including point shapes. [SIA2](https://www.ivoa.net/documents/SIA/20151223/REC-SIA-2.0-20151223.html)
  defines POS in ICRS degrees and TIME in MJD UTC. A zero-radius CIRCLE was verified
  against IRSA for M101, giving 52 DR1 D2 rows; it is point containment, not a
  finite-radius overlap approximation.

Option A: retain the verified ADQL and fix session isolation/diagnostics. Reordering
alone was not justified by the probes and did not restore data access.
Option B: official async is supported, but an environment-proxied minimal creation
probe returned 502 with no job Location; no job identifier was returned for cleanup. Async was
not selected: it adds polling/lifecycle complexity and no evidence showed it cures
the data-path issue within a hackathon HTTP budget. This does not mean async is
unsupported by IRSA.
Options C/D: reuse the PoC's verified SIA2 metadata route, with point containment and
strict time/reference validation, as a bounded explicit fallback.

## Selected correction

1. TapSession uses a fresh per-query Requests session, finite timeout and redirects
   disabled. It defaults to trust_env=False for public IRSA, isolating inherited
   proxies/netrc. Set IRSA_TAP_TRUST_ENV=true in the process environment if a deployment
   intentionally requires its proxy/CA settings. No global environment is changed.
2. Transport/service failures are classified as TapUnavailable; scientific validation
   errors and TAP QUERY_STATUS errors do not silently trigger another provider.
3. IrsaTemporalCatalog attempts native TAP once with a socket-read budget capped at
   10 seconds, then invokes IrsaSiaTemporalCatalog only for eligible failures.
4. SIA2 queries the four existing QR3/DR1 wide/deep collections separately with:

```text
GET https://irsa.ipac.caltech.edu/SIA
COLLECTION=spherex_dr1
POS=CIRCLE 210.80227 54.34895 0
RESPONSEFORMAT=VOTABLE
```

5. All collections must return valid, non-truncated VOTables. The existing 32 MiB
   response cap and MJD-day checks are reused. Exact detector filtering is local.
   Missing/inverted/non-finite time bounds, unexpected collection or unauthorized
   references fail closed. Only URLs returned by IRSA are used; no observation paths
   are guessed. Native URL/SSRF protections remain.
6. One retry after 0.5 s is allowed per collection only for connection/read failures
   and HTTP 502/503/504. Persistent failure rejects the whole discovery; no partial
   set is presented as a complete chronological prefix.
7. Deduplicate by the existing computed product_id, sort by lower observation time,
   artifact URI and ID, and select the first requested N. This is not pagination,
   a total-count API or sampling across the full available interval.

Connection timeout remains 15 s. Native direct adapter read timeout uses
IRSA_TIMEOUT_SECONDS clamped to 1-120 s. The endpoint's native attempt caps that at
10 s; each SIA request caps it at 30 s. There is at most one native attempt plus
four collections with at most two attempts each. Timeouts are socket connection/read
inactivity limits, not an end-to-end deadline or unlimited polling. Responses are
bounded; no FITS are downloaded during discovery. Deployments must budget HTTP
worker/client deadlines for this path.

## Query, time semantics and public compatibility

A working native TAP diagnostic was:

```sql
SELECT TOP 1 table_name FROM TAP_SCHEMA.tables
```

The scientifically valid SPHEREx ADQL is retained unchanged, but did NOT return data
within the native TAP test budget:

```sql
SELECT TOP 5
  'https://irsa.ipac.caltech.edu/' || a.uri ||
  '?center=210.80227,54.34895d&size=0.1' AS uri,
  a.uri AS artifact_uri, p.time_bounds_lower
FROM spherex.artifact a
JOIN spherex.plane p ON a.planeid = p.planeid
WHERE 1 = CONTAINS(POINT('ICRS', 210.80227, 54.34895), p.poly)
  AND p.energy_bandpassname = 'SPHEREx-D2'
ORDER BY p.time_bounds_lower, a.uri
```

The fallback maps real access_url to artifact_uri/cutout_url and real t_min to the
existing time_bounds_lower field. SIA2 t_min is acquisition start in MJD days/UTC,
with t_max validated as interval end. Native TAP values remain original numeric
MJD-day bounds; this fix does not invent a verified native TAP time scale or convert
processing DATE into acquisition time. BE-02 still verifies timestamps from FITS.

The response stays `{count, results}`. Each result retains product_id, artifact_uri,
cutout_url, time_bounds_lower, ra_deg, dec_deg, size_deg and detector. The SHA-256
product_key algorithm is unchanged, as are existing stored IDs and detail lookup.
IDs remain query-associated references, not universal exposure identities; different
provider precision/processing versions can produce distinct references. Native and
SIA2 metadata are not claimed to have identical publication/ingestion coverage.
SIA2 may lag archive ingestion. The source route is explicit in server logs and API
documentation; no extra JSON field breaks SourceProduct(**data).

## Executed verification

Deterministic regression command:

```cmd
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp C:\Users\Angle\AppData\Local\Temp\eon-tap-fix-pytest-final
```

**89 passed, 5 optional integrations skipped.** Ten focused regressions cover proxy
contamination, eligible fallback, invalid scientific data, exact zero-radius
selection, four collections, ordering/dedup, DTO compatibility and bounded retry.
The first full-suite attempt encountered permissions on the shared pytest temp
folder; the dedicated temporary base resolved it without code/database changes.
Focused Ruff checks passed. Django check and OpenAPI validate/fail-on-warn passed
without warnings. The existing smoke command also passed all five checks:

```cmd
.venv\Scripts\python.exe -B -m unittest discover -s tests -p smoke_stdlib.py
```

Native real TAP: limit=1 was attempted with the existing full query and a direct
60-second session, and timed out. Per the requested sequence, native limit=5 was
not declared successful or used to claim a native integration pass.

Standalone SIA point discovery limits 1/5 succeeded in approximately 13.1/9.4 s.
Its primary was explicitly marked already unavailable in that isolated route probe;
this is real SIA traffic, not evidence of a second native TAP execution.

Real application/HTTP validation command:

```cmd
.venv\Scripts\python.exe -B -u -m scripts.validate_temporal_fix
```

Port 8001 was no longer listening during this task. The helper started its own
Observation process on 127.0.0.1:18001 with the existing configured Observation
PostgreSQL/Redis and process-only environment values; it stopped that process in
finally. No user process was killed or restarted, no migration was executed and no
other service database was accessed. Normal Observation API calls persisted metadata.

Final verified results:

| Request | Result | Elapsed | Detail/BE-02 resolver |
| --- | --- | --- | --- |
| M101, size_deg=0.1, D2, limit=1 | HTTP 200, count=1 | 16.409 s | All returned objects matched |
| Same, limit=5 | HTTP 200, count=5 | 18.184 s | All five matched |

Server logs confirm a native ReadTimeout followed by real SIA2 discovery: 84 unique
products available in that run, from which 1/5 chronological references were selected.
No count is a fixed test expectation. Earlier application probes saw transient SIA
502s; the bounded retry was added and tested rather than ignoring failed collections.

[http_validation.json](evidence/TAP_discovery_fix/http_validation.json) records the
actual response bodies and safe route logs. Standalone route evidence is in
[sia_point_limit_1.json](evidence/TAP_discovery_fix/sia_point_limit_1.json) and
[sia_point_limit_5.json](evidence/TAP_discovery_fix/sia_point_limit_5.json).
The first real product is:

```json
{
  "product_id": "803628b15eeaa57e572cf8598be71893281021f9dfb44b3574d7b0af3e89bd03",
  "time_bounds_lower": 60805.711548,
  "detector": "SPHEREx-D2"
}
```

Its full object and all five complete references are in the HTTP evidence. Every
returned reference passed the existing BE-02 URL policy; each detail was fetched
through ObservationApiResolver and compared with the search object. This validates
real HTTP, persistence and cross-service DTO compatibility, not just mocks.

## Files and integration into BE-02

Changed Observation files:

- services/observation/infrastructure/tap.py: session isolation, typed failure,
  per-request safe telemetry and optional native read cap.
- services/observation/infrastructure/irsa.py: backward-compatible reuse of SIA table
  retrieval with an explicit point radius and per-call session.
- services/observation/infrastructure/temporal.py: new bounded provider/fallback adapter.
- services/observation/presentation/views.py: provider composition and accurate API descriptions.

Tests/helper/documentation:

- tests/test_temporal_fallback.py.
- scripts/validate_temporal_fix.py.
- docs/TAP_DISCOVERY_FIX.md and docs/IRSA_INTEGRATION.md.
- docs/evidence/TAP_discovery_fix/{diagnostics,http_validation,sia_point_limit_1,
  sia_point_limit_5,be02_request}.json.

No new dependency, migration or public required field was added. No Jobs, Compose,
.env, Image Processing implementation or BE-02/BE-03 manifest was modified. No commit,
push or merge was performed. After review, integrate these isolated Observation files
into the BE-02 environment using the team's normal workflow; restart the actual
Observation server with this version. The temporary validation server is stopped.

[be02_request.json](evidence/TAP_discovery_fix/be02_request.json) contains five genuine
stored IDs and matching geometry. Its job_id is only a caller-supplied validation
label and can be replaced by BE-03. The IDs were stored through Observation API, not
seeded through Image Processing or direct database access.

BE-02 can then retry its existing real integration using those references and the
configured eon-science bucket. FITS acquisition, PNG generation, S3 persistence,
manifest retrieval and BE-03 lifecycle were NOT rerun in this isolated fix task.
Native SPHEREx TAP availability remains an external limitation; the hackathon
endpoint is unblocked by verified, explicitly documented SIA2 metadata discovery.
