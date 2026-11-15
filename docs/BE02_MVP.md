# EON-BE-02: FITS Processing and Timelapse MVP

## 1. Executive summary

BE-02 now provides a bounded processing use case and Celery adapter for:

**Observation references -> authorized IRSA cutouts -> FITS/WCS -> chronological
PNG frames -> S3 objects -> result manifest -> BE-03.**

The complete bounded application pipeline has now passed against the corrected
Observation endpoint, real IRSA FITS cutouts and local MinIO (`eon-science`). Five
BE-01 references were resolved over HTTP, downloaded, aligned, rendered, uploaded
and retrieved with a valid BE-03 manifest. **end_to_end_passed=true** applies to this
real BE-02 application pipeline. Celery dispatch and Jobs lifecycle were not tested.
Native TAP remains unavailable; corrected discovery supports bounded SIA2 fallback.
See the current revalidation below; earlier blocked runs remain historical evidence.
The separate real HTTP cutout workflow also passed after local environment recovery;
see section 17 for job, cutout, storage, error-handling and cleanup evidence.

## 2. Scope and exclusions

Inputs: 1-10 previously discovered product IDs, one detector and one small square
ICRS region. Target 5-10 frames, but one or fewer-than-five suitable observations
are supported without inventing data. Duplicate inputs are removed deterministically.

Not implemented: discovery, full-sky processing, spectral cubes, 102 channels,
photometry, GIF/MP4, frontend playback, Jobs lifecycle/progress, new job submission
endpoints, presigned/public URLs, or a new S3 service. Existing cutout APIs remain.
No changes were made to Observation, Jobs, compose.yaml, .env or migrations.
Pillow is the only new dependency, used for PNG encoding/annotation. Small inverse
WCS mapping and bilinear interpolation use existing Astropy/NumPy dependencies.

## 3. Architecture and responsibilities

- Domain: TimelapseRequest validates bounded input and produces a canonical request
  fingerprint. It has no Django, HTTP, Celery, Astropy or storage dependencies.
- Application: ProcessTimelapse resolves, acquires, delegates rendering, uploads and
  returns results via existing ProductResolver, CutoutDownloader and ScientificObjectStore
  ports plus the new SequenceRenderer port. It never accesses a database.
- Infrastructure: existing ObservationApiResolver, IrsaCutoutDownloader and
  S3ScientificObjectStore; new FitsSequenceRenderer and render_timelapse task.
- Observation owns discovery and product detail. BE-03 owns job IDs, submission,
  lifecycle, retries/progress and delivery URLs. Image Processing owns output artifacts.

The task returns results through the existing Celery result mechanism. It does not
submit another job, update Jobs tables or call update_state. The optional direct
management command is a diagnostic/processing entry point, not an orchestrator.

## 4. Processing pipeline

1. Validate request: job_id, 1-10 SHA-256 product references, finite region and detector.
2. Resolve and authorize ALL references before any download. Reject missing product,
   mismatched ID/detector/region or unauthorized URL.
3. Sequentially download each server-side cutout into a temporary directory.
4. Validate IMAGE/FITS metadata, observation times, units, dimensions and celestial WCS.
5. Reproject onto one north-up, east-left TAN grid at 6.2 arcsec/pixel.
6. Deduplicate acquisitions and sort verified UTC times, with product_id as tie-breaker.
7. Compute one shared intensity scale and generate 512x560 RGB PNGs with date/detector labels.
8. Upload retained source FITS and PNG frames; upload manifest.json LAST.
9. Return the agreed completed manifest only if every required upload succeeds.

Failures raise to the caller: there is no silent synthetic replacement or skipped
invalid input. Required inputs failing acquisition or processing fail the run.
Duplicate acquisitions are the documented exception, not processing failures.
Temporary files are removed on success/failure. Uploaded partial objects may remain;
BE-03/storage operations should define retry and abandoned-prefix cleanup policies.

## 5. FITS acquisition and validation

Uses the exact cutout_url/artifact_uri returned by Observation, not frontend URLs.
Existing URL policy enforces HTTPS, exact IRSA hostname, approved DR1/QR3 level2
paths, .fits suffix, matching center/size, no traversal, credentials, custom port,
fragments or unexpected query parameters. Numeric URL parameters must also be finite.
Redirects are not followed. No full-detector fallback was added.

Bounds for this processing path:

| Resource | Bound |
| --- | --- |
| Product identifiers per request | 1-10 before deduplication |
| Region side | >0 and <=0.2 degrees |
| Downloaded FITS per input | 8 MiB default; application override capped at 16 MiB |
| Download timeout | Existing downloader: 45 s socket timeout, three attempts/backoff |
| Science IMAGE dimensions | 1-512 pixels on each axis |
| Output science grid | <=256 pixels on each axis; current size limit gives <=117 |
| PNG | 512x560 RGB |
| Celery adapter | time_limit=600, pool/platform dependent |
| S3 connection/read | 5/30 s, bounded SDK retries |

The urllib timeout bounds socket connection/reads, not total elapsed transfer time.
FITS bytes never enter JSON or task messages. IMAGE dimensions are checked before
loading/scaling its array. Inputs must have an explicitly 2D IMAGE; no arbitrary cube
plane is selected. FLAGS/VARIANCE, if present, must match IMAGE shape. Source FITS
retain all extensions and are persisted alongside PNGs. There is no new FITS cache;
retries re-fetch bounded inputs and reuse deterministic output keys.

## 6. FITS/WCS region processing

The requested center must lie inside each source IMAGE. Missing celestial WCS,
invalid units, missing IMAGE, malformed FITS or no valid reprojected coverage fail.
IMAGE brightness must be convertible to MJy/sr. NaN/infinite data and invalid/negative
VARIANCE pixels are excluded. Bilinear interpolation conservatively retains missing
coverage as NaN; display-only missing pixels use RGB (55,70,85), not scientific zero.

The common TAN grid is fixed at the request center, north up/east left, with dimensions
rounded upward. Objects are not independently recentered. Spatial SIP is retained.
This small NumPy inverse-mapping implementation is for visualization, not precision
photometry or flux-conserving resampling. Native grids are nearest-neighbor enlarged
to the browser panel; that is not new scientific resolution.

WCS-WAVE, when present, is evaluated at the requested sky coordinate using WCS key W
with fobj=hdul; SIP is disabled only for that spectral WCS. Central wavelength and
bandwidth in micrometres are preserved in optional provenance. Unknown spectral
metadata remains null rather than being invented. FLAGS MSKN labels are retained
without guessing meanings; no calibrated quality-bit masking is claimed.

## 7. Temporal ordering and shared normalization

BE-01 currently exposes time_bounds_lower without a verified epoch/scale contract.
BE-02 preserves it unconverted and derives the frame time from FITS DATE-OBS or
MJD-OBS with TIMESYS. Absent TIMESYS uses the FITS UTC default, recorded explicitly.
DATE processing metadata is never substituted. Date-only DATE-OBS is rejected;
DATE-OBS and MJD-OBS must agree within one second when both are present.

Detector is checked against the reference. OBSID is required, and when the documented
artifact filename contains an OBSID/detector they must match the FITS headers.
FITS acquisition times are converted using Astropy to ISO UTC and used for sorting.
Equal times use product_id for deterministic ordering. A single acquisition time is
reported as such; no distinct dates, interpolated measurements or frames are invented.

Duplicate product IDs resolve/download only once. Multiple products with the same
OBSID, detector and verified acquisition timestamp produce one frame: highest
numeric l2b-v path version wins, then descending product_id. Discarded references
are recorded. Distinct OBSIDs with equal times remain distinct frames.

All retained frames share percentiles 1/99.5 and an asinh stretch (a=0.1), computed
from their finite pixels. Limits, grid, coverage, time source, source SHA-256, original
URLs, FITS version and raw TAP value are persisted in provenance. Constant-intensity
sequences use a deterministic nonzero scale width. Different wavelengths are explicitly
exploratory: brightness changes are not evidence of motion or intrinsic variability.

## 8. MinIO/S3 configuration

The selected development image is alpine/minio:RELEASE.2025-10-15T17-29-55Z,
run independently of Compose. No adapter imports image-specific libraries.

```dotenv
S3_ENDPOINT_URL=http://127.0.0.1:9000
S3_ACCESS_KEY=<configured-locally>
S3_SECRET_KEY=<configured-locally>
S3_BUCKET=<configured-locally>
```

Credentials and bucket are read from environment, never hardcoded or logged.
Generated keys use:

```text
timelapses/{job_id}/{request_fingerprint}/source-{product_id}.fits
timelapses/{job_id}/{request_fingerprint}/frame-000.png
timelapses/{job_id}/{request_fingerprint}/manifest.json
```

The fingerprint includes canonical product IDs/region/detector and pipeline version.
It prevents different input sets reusing one job_id from overwriting each other.
Content types are application/fits, image/png and application/json respectively.
The optional provenance.manifest_object_key identifies the persisted JSON. BE-03
can sign frame object keys using its configured bucket; there are no localhost URLs
in the shared manifest.

Create the configured bucket once after the service is reachable, using the existing
adapter/SDK (not an automatic side effect of processing):

```cmd
python -c "from services.image_processing.infrastructure.storage import S3ScientificObjectStore; s=S3ScientificObjectStore(); s.client.create_bucket(Bucket=s.bucket)"
```

If it already exists, verify access with head_bucket instead. Do not run this command
against an unintended bucket/account. Integration tests should use a dedicated bucket.

## 9. BE-03 integration contract

Application boundary:

```python
ProcessTimelapse(resolver, downloader, storage, renderer).execute(request)
```

Existing async infrastructure, new processing-only task:

```python
app.send_task(
    "eon.image_processing.render_timelapse",
    kwargs={
        "job_id": existing_job_id,
        "product_ids": previously_discovered_product_ids,
        "ra_deg": 210.80227,
        "dec_deg": 54.34895,
        "size_deg": 0.1,
        "detector": "SPHEREx-D2",
    },
    task_id=existing_job_id,
    queue="image_processing",
)
```

BE-03 supplies the ID; BE-02 does not create or track another job. The existing Jobs
HTTP allowlist only accepts create_cutout and its old payload. BE-03 must allow/route
render_timelapse and validate this request in its own implementation. Those files
were deliberately not modified during concurrent development.

Required shared result remains exactly job_id/status/frames, and each frame retains
index/product_id/timestamp/object_key. The ONLY additive field is optional provenance,
with scientific metadata and manifest key; consumers may ignore it. This additive
extension is documented here for review, not a requirement that BE-03 change its
agreed required-field schema. Celery success returns the manifest dictionary;
exceptions mean processing failed. BE-03 decides lifecycle state/progress/retry policy.

## 10. Request/response and errors

Example request JSON, with placeholders that MUST be replaced by real BE-01 IDs:

```json
{
  "job_id": "existing-job-001",
  "product_ids": ["<64-character Observation product_id>"],
  "ra_deg": 210.80227,
  "dec_deg": 54.34895,
  "size_deg": 0.1,
  "detector": "SPHEREx-D2"
}
```

job_id, product_ids and region fields are required; detector defaults to SPHEREx-D2.
Job IDs permit 1-128 safe ASCII identifier characters, no slash/traversal. Identical
request products are canonicalized; arbitrary URLs/unknown task kwargs are not accepted.
No new HTTP endpoint was needed. Existing /api/v1/cutouts/ APIs are unchanged.

Illustrative result (not evidence of an uploaded object):

```json
{
  "job_id": "existing-job-001",
  "status": "completed",
  "frames": [
    {
      "index": 0,
      "product_id": "<actual Observation reference>",
      "timestamp": "2025-05-10T17:04:37.741Z",
      "object_key": "timelapses/existing-job-001/<fingerprint>/frame-000.png"
    }
  ]
}
```

Validation/unknown product/geometry/scientific failures raise ValueError. Observation
HTTP/network, downloader and storage errors propagate to BE-03/Celery; there is no
completed manifest returned after a failed upload. The direct command wraps errors
as CommandError/nonzero exit. The optional evidence-preview script does NOT return
a Jobs completed manifest because it never resolves through BE-01 or stores outputs.

## 11. Windows CMD and Docker startup

From a checkout with the integrated BE-01 contracts:

```cmd
.venv\Scripts\activate.bat
python -m pip install -e ".[dev]"
set SERVICE_NAME=image_processing
set POSTGRES_HOST=127.0.0.1
set REDIS_URL=redis://127.0.0.1:6379/0
set OBSERVATION_SERVICE_URL=http://127.0.0.1:8001
set S3_ENDPOINT_URL=http://127.0.0.1:9000
python manage.py check
python manage.py render_timelapse --request-file request.json --manifest-file result.json
```

Export your local secrets and S3_BUCKET before processing. manage.py does not load
.env automatically. Start Observation separately with SERVICE_NAME=observation and
`python manage.py runserver 8001`; product IDs must already exist in its repository.
No Observation/Jobs database is read directly by the processor.

For a local worker, BE-03 can invoke the existing queue. On Windows use a suitable
pool for development, e.g. `celery -A eon_common.celery worker -Q image_processing
--pool=solo --loglevel=INFO`. Pool/platform limitations apply to hard time limits;
the network/file/frame bounds still apply. Prefer a Linux worker for deployment.

The image publisher documents the following standalone server invocation; this is
an operator setup command, not a successful-startup claim for the configured service:

```cmd
docker run -d --name eon-minio-local -p 127.0.0.1:9000:9000 -p 127.0.0.1:9001:9001 -e MINIO_ROOT_USER=%S3_ACCESS_KEY% -e MINIO_ROOT_PASSWORD=%S3_SECRET_KEY% -v eon_minio_data:/data alpine/minio:RELEASE.2025-10-15T17-29-55Z server /data --console-address :9001
```

[Image publisher usage](https://hub.docker.com/r/alpine/minio/) confirms the server
command. The image was present locally and its entrypoint inspected as minio; no
permanent service was started. Check health and bucket access before live tests.
Do not edit Compose to start this independent service.

The existing Dockerfile can run a Linux image worker without Compose:

```cmd
docker build -t eon-be02 .
docker run --rm --env-file .env -e SERVICE_NAME=image_processing -e OBSERVATION_SERVICE_URL=http://host.docker.internal:8001 -e REDIS_URL=redis://host.docker.internal:6379/0 -e S3_ENDPOINT_URL=http://host.docker.internal:9000 eon-be02 celery -A eon_common.celery worker -Q image_processing --concurrency=1 --loglevel=INFO
```

These deployment commands were not executed as an end-to-end stack. Native Windows
uses 127.0.0.1; Docker Desktop workers use the host gateway for host services. Keep
configured credentials private. No service/container deployment is an implicit
requirement of the deterministic test suite.

## 12. Tests and executed evidence

```cmd
python -B -m pytest -q -p no:cacheprovider
python -B -m unittest discover -s tests -p smoke_stdlib.py
```

**Final deterministic results: 132 passed, 6 optional integrations skipped.**
The five smoke checks also passed. Focused Ruff checks and git diff --check passed;
Django check with SERVICE_NAME=image_processing reported no issues. The direct
render_timelapse command help was verified without accessing a database. Fixtures are explicitly synthetic and include WCS
rotation, invalid dimensions/planes, missing times, TAI conversion, NaN/Inf coverage,
shared intensity mapping, duplicates/ties, storage failures, content types, resolver
errors, unsafe sources and redirects. No Redis/PostgreSQL/MinIO is required by them.

Real partial preview, reproducible from the existing PoC cache/catalog:

```cmd
python -B -m scripts.validate_be02_preview --fits-dir ..\SPHERExProofOfConcept\.cache --sia-xml ..\SPHERExProofOfConcept\sia_result.xml --output-dir docs/evidence/BE02_preview --maximum 5 --download-one
```

It matches cache URL hashes to actual SIA URLs, optionally re-downloads one bounded
IRSA cutout and processes five real files. It uses LOCAL FIXTURE identifiers, not
fabricated BE-01 discovery IDs, and explicitly reports end_to_end_accepted=false,
observation_service_resolved=false and storage_verified=false. It is fixed to the
M101/D2/0.1-degree control region and is not a production discovery fallback.

Verified preview timestamps:

- 2025-05-10T17:04:37.741Z
- 2025-05-10T17:08:55.602Z
- 2025-05-15T21:02:00.700Z
- 2025-05-16T05:09:09.595Z
- 2025-05-16T13:21:54.629Z

[Preview evidence](evidence/BE02_preview/validation.json) preserves source metadata,
hashes, grid, normalization and proposed keys. Five adjacent PNGs are real scientific
previews, not uploaded S3 objects. One fresh IRSA download succeeded in this run.

**Historical attempt, superseded by the successful configured-bucket check below:**
[isolated S3 validation](evidence/BE02_S3_validation.json): a temporary MinIO container
on port 19000 with random credentials was started using the selected local image.
Its published-port health check did not succeed, so upload/retrieval was not reached.
The temporary container was stopped/removed; existing PostgreSQL/Redis containers,
.env, configured credentials/bucket and Compose were untouched. This is a failed
integration attempt, not a passed storage test.

The live complete test is opt-in and requires real Observation product IDs and S3:

```cmd
set EON_RUN_BE02_LIVE=1
set EON_BE02_REQUEST_FILE=request.json
python -B -m pytest -q -p no:cacheprovider tests/integration/test_timelapse_live.py
```

It resolves actual BE-01 products, acquires FITS, uploads results, downloads each PNG
and the manifest, and checks Content-Type/bytes. It intentionally retains objects
for inspection: use a dedicated job ID and bucket. It was NOT executed in the initial
run because services were unavailable. In the follow-up the prerequisite discovery
failed before a request file with real product IDs could be produced; the complete
pytest integration therefore still could not run. Default skipped tests are not successes.

### Historical blocked follow-up: November 14, 2026 (superseded below)

Command executed from the repository root with the existing backend venv:

```cmd
.venv\Scripts\python.exe -B -u -m scripts.validate_be02_integration --load-env
```

The helper reads only missing S3 credentials from .env into process memory; it does
not edit .env. It explicitly uses the requested localhost endpoints/eon-science bucket
and bypasses proxies for localhost in that process. No services were restarted or
modified. No Observation or Jobs database was accessed directly. No local fixture
product IDs were substituted for BE-01 IDs.

Results, recorded in [validation.json](evidence/BE02_integration/validation.json):

| Stage | Result |
| --- | --- |
| GET http://127.0.0.1:8001/health/ | Passed: HTTP 200, service=observation |
| GET http://127.0.0.1:9000/minio/health/live | Passed: HTTP 200 |
| head_bucket eon-science through the existing S3 adapter | Passed |
| Independent upload/retrieval of five existing real-data PNG previews | Passed: 68,415 bytes total; identical SHA-256/bytes after retrieval |
| PNG validation and S3 metadata | Passed: five valid PNGs, 512x560, Content-Type image/png |
| GET /api/v1/observations/temporal/ for M101/D2, limit=5 | Failed: HTTP 502 on initial request and one bounded retry |
| Resolve individual BE-01 product IDs | Not run: discovery supplied no valid IDs |
| Fresh FITS acquisition and sequence generation through BE-01 | Not run in this follow-up |
| Processing-output uploads and shared manifest retrieval | Not run: dependent on successful discovery |

Exact discovery request:

```http
GET http://127.0.0.1:8001/api/v1/observations/temporal/?ra_deg=210.80227&dec_deg=54.34895&size_deg=0.1&detector=SPHEREx-D2&limit=5
```

Both attempts returned:

```json
{"detail": "IRSA TAP query unavailable"}
```

The first response is preserved in [discovery.json](evidence/BE02_integration/discovery.json),
and the retry/stage report in validation.json. This establishes the failing stage
and API response; it does not establish the underlying IRSA exception or its cause,
which is not exposed by that response. Observation health alone does not establish
that its upstream TAP request succeeds.

The five successful storage-only objects remain under:

```text
validation/be02-storage-c83fa3e281194790a4728f51b87d7165/frame-000.png
validation/be02-storage-c83fa3e281194790a4728f51b87d7165/frame-001.png
validation/be02-storage-c83fa3e281194790a4728f51b87d7165/frame-002.png
validation/be02-storage-c83fa3e281194790a4728f51b87d7165/frame-003.png
validation/be02-storage-c83fa3e281194790a4728f51b87d7165/frame-004.png
```

Their object keys, hashes, dimensions, byte sizes and content types are recorded in
validation.json. They are the previously verified scientific previews, uploaded
with the production S3 adapter for this storage check. They are NOT outputs from a
new BE-01-resolved processing run, and no completed Jobs manifest was fabricated.
Existing bucket objects were not deleted or overwritten.

Additional deterministic checks executed in this follow-up:

```cmd
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_timelapse.py::test_single_realistic_fixture_and_persisted_manifest tests/test_timelapse.py::test_multiple_frames_chronological_indices_and_determinism tests/test_timelapse.py::test_upload_failures_never_return_completed_manifest
.venv\Scripts\python.exe -B -m ruff check --no-cache scripts/validate_be02_integration.py
```

Result: five deterministic checks passed; lint passed. These verify shared manifest
fields, chronological indices and upload-failure behavior with synthetic fixtures
and mocked ports. They are not successful live manifest verification.

The validation helper exits nonzero with failed_stage=temporal_discovery and
end_to_end_passed=false. Its downstream live path remains unexecuted. Restore TAP
backed discovery (or supply genuine previously discovered BE-01 IDs), then rerun the
helper/full opt-in test to verify FITS acquisition, processing-output persistence and
the actual retrieved BE-03 manifest. Do not replace those IDs with SIA/local fixture hashes.

### Current end-to-end revalidation: November 14, 2026 local / November 15 UTC

Executed on `feature/EON-BE-02`, HEAD `3e5e15b`. `git merge-base --is-ancestor
 eb675a9 HEAD` returned zero, confirming the Observation fallback fix is included.
The working tree was clean before this validation. No service was restarted.

Commands executed (existing venv, repository root):

```cmd
.venv\Scripts\python.exe -B -u -m scripts.validate_be02_integration --load-env --output-dir docs/evidence/BE02_revalidation
.venv\Scripts\python.exe -B -u -m scripts.audit_be02_revalidation
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp C:\Users\Angle\AppData\Local\Temp\eon-be02-revalidation-tests --tb=short
.venv\Scripts\python.exe -B -m unittest discover -s tests -p smoke_stdlib.py
.venv\Scripts\python.exe -B -m ruff check --no-cache scripts/audit_be02_revalidation.py
```

The helpers read only missing S3 credentials from `.env` into process memory;
credentials are never recorded. The audit helper enables `EON_RUN_BE02_LIVE=1`
and sets `EON_BE02_REQUEST_FILE` to the saved real request before running
`tests/integration/test_timelapse_live.py` in a child process. This live test executes
the complete production adapter pipeline again for the same five products and
validates retrieval. It deliberately retains/reuses this validation job prefix.

[Current validation](evidence/BE02_revalidation/validation.json),
[real request](evidence/BE02_revalidation/request.json),
[result manifest](evidence/BE02_revalidation/manifest.json),
[object audit](evidence/BE02_revalidation/object_audit.json) and
[live pytest output](evidence/BE02_revalidation/live_pytest.txt) are the current evidence.
The previous `BE02_integration` files remain historical failed evidence.

The bounded HTTP request used M101 RA=210.80227, Dec=54.34895, size=0.1 degrees,
SPHEREx-D2, limit=5. It returned HTTP 200 and five genuine DR1 references, all resolved
through `ObservationApiResolver`. This is a fresh endpoint request, not proof of a
cache miss or new upstream query: the response does not expose the discovery route.
The merged implementation supports bounded SIA2 fallback. Native TAP was not
re-probed and is not reported as successful.

Five real cutouts (each below 8 MiB) produced five chronological PNGs spanning
`2025-05-10T17:04:37.741Z` through `2025-05-10T21:58:09.061Z`. There are five distinct
acquisition times on the same UTC date; no claim of different calendar dates or
visible motion is made. FITS DATE-OBS/TIMESYS determine output times. The largest
catalogue MJD-versus-FITS discrepancy was 0.03720 seconds. The legacy provenance
field `tap_time_interpretation` remains conservative for wire compatibility; it does
not mean that native TAP supplied this run or that FITS DATE is an observation time.

The audit retrieved all five retained source FITS and checked their SHA-256 against
processing provenance. It revalidated celestial mappings onto the common 59x59 TAN
grid (north up/east left); maximum source-WCS sky roundtrip error was 0.000002017
arcsec. This tests coordinate consistency, not external astrometric accuracy or
source-centroid registration. Shared pooled limits were vmin=0.6057568590745926,
vmax=6.1455548003325635 MJy/sr, with asinh a=0.1. Every retrieved PNG science panel
exactly matched the independently reconstructed shared-scale pixels, including the
orientation and invalid-coverage colour. Five retrieved PNGs total 67,368 bytes;
all are valid 512x560 RGB PNGs with `Content-Type: image/png`. Copies and hashes are
saved alongside the evidence. The stored final JSON manifest equals the returned
manifest, preserves the BE-03 required fields and has ascending timestamps/indices.

Astropy emitted the existing SIP diagnostic while constructing the alternate
spectral WCS. The renderer clears SIP on that spectral WCS and preserves the spatial
WCS. The real numerical checks passed; no header alteration or new WCS policy was
introduced. FLAGS labels are retained but bits are not interpreted as a calibrated
mask. Varying central wavelengths and incomplete coverage remain explicit provenance;
a shared scale does not establish photometric variability or spectral equivalence.

All required application-pipeline stages passed; no application stage failed or was
skipped. Celery broker/worker dispatch, Jobs lifecycle and five other opt-in integrations
were not run. The default regression suite reported **142 passed, 6 skipped**;
the BE-02 live test was enabled separately and reported **1 passed in 33.70s**.
Five smoke checks and focused lint passed. No changes were made to compose.yaml, .env, Observation, Jobs or unrelated
services. No commit or push was performed.

## 13. Known limitations and acceptance checklist

| Acceptance criterion | Current evidence/status |
| --- | --- |
| Merged Observation discovery fix present | Passed: branch feature/EON-BE-02, HEAD 3e5e15b; eb675a9 is an ancestor |
| Observation and MinIO accessible | Passed: both health endpoints HTTP 200; head_bucket succeeded |
| Resolve real products through BE-01 HTTP | Passed: five IDs from a fresh bounded temporal endpoint request |
| Acquire real SPHEREx cutouts | Passed: five fresh IRSA downloads, 19,990,080 bytes total |
| Common celestial grid | Passed: 59x59 ICRS TAN, 6.2 arcsec/pixel; audited source WCS roundtrips |
| Shared visual scale | Passed: pooled percentiles and exact PNG science-panel pixel audit |
| Chronological real PNG frames | Passed: five distinct FITS acquisition times, 512x560 RGB PNGs |
| Store and retrieve outputs | Passed: five source FITS, five PNGs and final manifest in eon-science |
| Agreed BE-03 result fields | Passed: job_id/status/frames and index/product_id/timestamp/object_key; retrieved manifest equals returned result |
| Regression and live tests | Passed: 142 default tests, five smoke checks, plus the separately enabled live test; six optional tests skipped in default run |
| BE-03 worker/lifecycle integration | Skipped: outside this direct processing validation; no Jobs changes |
| Native TAP discovery | Not passed: remains a known timeout; no native success claimed |

The bounded BE-02 application pipeline is accepted for this real run. This does not
establish Celery/Jobs orchestration acceptance. No calibrated photometry/quality mask,

PSF matching, background subtraction or motion detection is implemented. A shared
scale does not make different spectral bands scientifically interchangeable. Source
VARIANCE is preserved, not propagated as resampling uncertainty. Retries can leave
partial prefixes; storage cleanup, delivery URLs and lifecycle remain external.

## 14. Troubleshooting

- 400/unknown references in BE-01: rediscover products with matching region/detector;
  never invent a hash or access its database from Image Processing.
- Observation 502/TAP timeout: BE-01 discovery must be healthy; do not replace TAP
  products with local SIA fixture IDs in the production pipeline.
- Local ports time out: verify service binding, Docker published ports, firewall and
  proxy/NO_PROXY configuration. Container presence does not prove Windows reachability.
- MinIO missing bucket/access denied: verify S3 variables, health, credentials and
  bucket permissions; processing does not silently create or switch buckets.
- Oversized/unsupported FITS: choose a smaller verified server cutout. There is no
  unbounded full-image fallback.
- Missing acquisition timestamp, detector or OBSID: fail rather than invent metadata.
- SIP INFO while loading WCS W: expected Astropy diagnostic; SIP is disabled on the
  spectral WCS before wavelength evaluation, preserved on the spatial WCS.
- Worker task unknown: use SERVICE_NAME=image_processing and image_processing queue;
  let BE-03 update its task allowlist/payload, not the existing cutout payload.
- S3 failure: no completed result is returned; inspect partial prefix/retry according
  to BE-03 policy. Never expose credentials in exception logs or manifests.

## 15. Next steps and changed documents/files

BE-03 should register/allowlist the processing task, provide the existing job ID,
validate bounded inputs, enqueue on image_processing, expose result/state and sign
frame keys only after successful completion. The frontend should consume ordered
frames/timestamps, respect irregular acquisition gaps, and surface provenance and
spectral/coverage limitations. MinIO and bucket access are now verified. Validate
BE-03 orchestration separately. The corrected BE-01 endpoint and bounded BE-02
application pipeline are now verified; native TAP success is not claimed.

Markdown changes: README.md was translated from Spanish to English because its
shared FITS/S3/setup description required updating. docs/BACKEND_WORKFLOW.md was
already English and received the BE-02 boundary section. This document is new.
AGENTS.md, BE01_MVP.md and unrelated/concurrently owned BE-03 documents were not
translated or edited. Repository-wide translation belongs in a separate docs ticket.

Complete implementation file inventory:

- pyproject.toml (Pillow dependency).
- services/image_processing/domain/timelapse.py (bounded request/fingerprint).
- services/image_processing/domain/security.py (reject non-finite URL parameters).
- services/image_processing/application/timelapse.py (ports and processing use case).
- services/image_processing/infrastructure/timelapse.py (FITS/time/WCS/PNG rendering).
- services/image_processing/infrastructure/storage.py (bounded S3 transport).
- services/image_processing/infrastructure/tasks.py (processing-only Celery adapter;
  existing cutout repository imported lazily).
- services/image_processing/management/__init__.py.
- services/image_processing/management/commands/__init__.py.
- services/image_processing/management/commands/render_timelapse.py (direct CLI).
- tests/test_timelapse.py and tests/test_timelapse_security.py (deterministic tests).
- tests/integration/test_timelapse_live.py (real service/storage test, opt-in).
- scripts/validate_be02_preview.py (explicit partial real-data preview).
- README.md, docs/BACKEND_WORKFLOW.md, docs/BE02_MVP.md.
- docs/evidence/BE02_S3_validation.json (failed isolated storage verification).
- docs/evidence/BE02_preview/validation.json.
- docs/evidence/BE02_preview/frame-000.png, frame-001.png, frame-002.png,
  frame-003.png and frame-004.png (five real data previews).


Follow-up validation additions: scripts/validate_be02_integration.py,
docs/evidence/BE02_integration/discovery.json and
 docs/evidence/BE02_integration/validation.json. Only this document and validation
artifacts/helper were added or updated during the follow-up; compose.yaml, .env,
Observation and Jobs were not modified. No commit or push was performed.

## 16. Historical HTTP cutout attempts (superseded by section 17)

**HTTP cutout acceptance remains blocked; http_cutout_flow_passed=false.** This does
not change the previous successful application-level timelapse validation. Neither
a complete HTTP cutout flow nor the BE-03 frontend workflow is accepted by this run.

The operator confirmed a correctly routed worker. The resumed validation independently
verified `eon-image-local@DESKTOP-E2NPNTE` consumes `image_processing` and registers
`eon.image_processing.create_cutout`. However, `GET http://127.0.0.1:8004/health/`
failed with `requests.ConnectionError` on both preflight attempts. There was no HTTP
response code from Jobs. No service was started, restarted or reconfigured.

| Stage | Result |
| --- | --- |
| PostgreSQL, eon_image_processing SELECT 1 | Passed |
| Redis 127.0.0.1:6379/0 PING | Passed |
| Observation :8001 health | Passed, HTTP 200 |
| Image Processing :8002 health | Passed, HTTP 200 |
| MinIO health and eon-science head_bucket | Passed; health HTTP 200 |
| Correct queue and task registration | Passed, live Celery inspect |
| Jobs :8004 connectivity | Failed, ConnectionError; no HTTP response |
| Bounded real temporal discovery, limit=1 | Passed, HTTP 200 |
| Real product detail | Passed, HTTP 200; identical to discovery result |
| Image API OpenAPI retrieval | Passed, HTTP 200; OpenAPI 3.0.3 snapshot saved |
| HTTP cutout POST and API-side product resolution | Blocked; zero POSTs sent because Jobs is unavailable |
| Job lifecycle, FITS processing and persistence | Blocked; no job_id/cutout_id was returned or invented |
| Metadata and FITS download endpoints | Blocked; no actual cutout identifier available |
| Actual payload versus OpenAPI comparison | Blocked; no HTTP cutout responses yet |

The genuine product is
`803628b15eeaa57e572cf8598be71893281021f9dfb44b3574d7b0af3e89bd03`, from DR1 D2.
The saved request uses RA=210.80227, Dec=54.34895 and size=0.1 degrees. This fresh
HTTP discovery request does not prove a cache miss or native TAP success. Native
TAP remains unverified; bounded SIA2 fallback is acceptable. Direct product-detail
success does not establish resolution from inside the running Image Processing API.

Commands executed:

```cmd
.venv\Scripts\python.exe -B -u -m scripts.validate_be02_http
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp C:\Users\Angle\AppData\Local\Temp\eon-be02-http-tests --tb=short
.venv\Scripts\python.exe -B -m unittest discover -s tests -p smoke_stdlib.py
.venv\Scripts\python.exe -B -m ruff check --no-cache scripts/validate_be02_http.py
```

Results: **142 passed, 6 optional integrations skipped**, five smoke checks passed,
and final helper lint passed. These automated regressions include synthetic/mock
ports; they do not establish real HTTP/Celery/FITS/S3 cutout completion. The live
infrastructure, Celery inspection and discovery checks above are separate real checks.

[Stage evidence](evidence/BE02_http_validation/validation.json),
[discovery](evidence/BE02_http_validation/discovery.json),
[product detail](evidence/BE02_http_validation/product.json),
[request](evidence/BE02_http_validation/request.json),
[OpenAPI snapshot](evidence/BE02_http_validation/openapi.json) and
[commands](evidence/BE02_http_validation/commands.txt) contain no credentials.

The helper consumes existing missing PostgreSQL/S3 credentials privately from .env;
localhost overrides apply only to its process, not the running APIs/worker. Its
preflight requires Jobs on :8004; it does not bypass Jobs or submit directly to Celery.
The downstream helper path is not yet live-verified. On a complete run it allows at
most one POST, polls at most 80 times within a 240-second monotonic budget and caps
retrieved FITS at 8 MiB. It distinguishes idempotent first-poll success from evidence
of fresh execution. API and worker endpoint environments still require operator
confirmation; health alone exposes no Redis/Observation settings.

Jobs must be reachable before resuming. In a separate Windows CMD with the existing
matching EON_INTERNAL_TOKEN and database credentials loaded, the required service
startup is (operator action; **not executed here**):

```cmd
call .venv\Scripts\activate.bat
set "SERVICE_NAME=jobs"
set "POSTGRES_HOST=127.0.0.1"
set "POSTGRES_DB=eon_jobs"
set "REDIS_URL=redis://127.0.0.1:6379/0"
python -B manage.py runserver 127.0.0.1:8004 --noreload
```

The Image Processing API must use JOB_SERVICE_URL=http://127.0.0.1:8004 and
OBSERVATION_SERVICE_URL=http://127.0.0.1:8001. After confirming configuration, rerun
the helper; inspect actual result IDs, lifecycle and downloaded FITS before accepting
the HTTP flow. No implementation defect has been demonstrated: this run stops at
an unavailable infrastructure dependency. No application correction, duplicate
endpoint or contract change was introduced. compose.yaml, .env, Observation, Jobs
and existing services were untouched. No commit, push or merge was performed.

### Resumed HTTP validation after Jobs startup (November 15, 2026 UTC)

**Current result: http_cutout_flow_passed=false.** The former unavailable-Jobs
preflight blocker is resolved. The failing stage is now the public Image Processing
submission: one real `POST /api/v1/cutouts/` returned HTTP **502**, with
`{"detail":"Job service unavailable"}`. No job_id/status_url was returned. There
was no valid-submission retry, asynchronous polling, FITS download or new cutout
persistence. The full HTTP cutout chain remains unverified.

| Stage | Actual resumed outcome |
| --- | --- |
| PostgreSQL SELECT 1 and Redis PING | Passed |
| Observation, Image Processing, Jobs and MinIO health | Passed, all HTTP 200 |
| eon-science head_bucket | Passed |
| Worker image_processing queue/create_cutout registration | Passed, eon-image-local worker |
| Image Processing and Jobs OpenAPI retrieval | Passed, both HTTP 200 |
| Bounded BE-01 discovery, limit=1, and real detail | Passed, both HTTP 200 |
| Public HTTP cutout submission | Failed, HTTP 502; no job_id/cutout_id |
| Direct Jobs authentication with existing diagnostic token | Failed, HTTP 401 |
| Jobs -> Celery execution and FITS acquisition | Blocked by submission failure |
| New cutout storage, metadata/FITS retrieval | Blocked; no result identifiers available |
| Successful cutout response/schema comparison | Blocked; no successful response payloads |
| Regression/smoke/lint | Passed: 142 tests, five smoke, focused lint; six optional tests skipped |

The product remains the genuine DR1/D2 reference
`803628b15eeaa57e572cf8598be71893281021f9dfb44b3574d7b0af3e89bd03`, discovered through
a fresh bounded HTTP request for M101 (RA=210.80227, Dec=54.34895, size=0.1). No
native TAP success or cache miss is inferred. The actual request/response is saved
in request.json/submission.json. Previous blocked preflight evidence is retained in
previous_blocked_validation.json; validation.json now describes this resumed run.

Executed commands:

```cmd
.venv\Scripts\python.exe -B -u -m scripts.validate_be02_http
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp C:\Users\Angle\AppData\Local\Temp\eon-be02-http-resumed-tests --tb=short
.venv\Scripts\python.exe -B -m unittest discover -s tests -p smoke_stdlib.py
.venv\Scripts\python.exe -B -m ruff check --no-cache scripts/validate_be02_http.py
```

The current [validation.json](evidence/BE02_http_validation/validation.json),
[submission.json](evidence/BE02_http_validation/submission.json),
[diagnostics.json](evidence/BE02_http_validation/diagnostics.json) and
[commands.txt](evidence/BE02_http_validation/commands.txt) preserve non-sensitive
outcomes. Additional bounded probes deliberately used invalid payloads so they
could not enqueue work:

- Real product with mismatched size=0.09 through Image Processing: HTTP 502,
  expected 400 after successful product resolution.
- Unknown random job UUID through Image Processing: HTTP 502, expected 404 if
  its Jobs forwarding works.
- Invalid cutout identifier: HTTP 400 as expected.
- Empty Jobs payload without authentication: HTTP 401 as expected.
- Empty Jobs payload with a nonempty existing diagnostic token loaded privately:
  HTTP 401. Correct authentication would allow serializer rejection (400), not
  enqueue an empty payload.

These observations demonstrate that internal forwarding/authentication is not
validated. They suggest local process configuration problems: direct service health
does not verify the URLs/token inherited by the running Image Processing process.
The diagnostic token was rejected by Jobs, but this does not prove which token the
running Image Processing API holds. Its generic catch also covers Observation
resolution, so the 502 label alone cannot identify whether Observation, Jobs URL,
authentication or another adapter error is the immediate cause. A redacted traceback
was requested from the running API. No exact underlying exception is invented.

No FITS-processing defect has been demonstrated and no speculative application
correction was made. Verify the running API's OBSERVATION_SERVICE_URL=:8001,
JOB_SERVICE_URL=:8004, localhost Redis and the matching EON_INTERNAL_TOKEN shared
with Jobs before retrying. Existing workers/services were not changed or restarted;
.env, compose.yaml, Observation and Jobs were untouched. No commit, push or merge.

The regression suite uses synthetic/mocked dependencies where applicable and its
six skipped integrations are not passed live tests. The real checks above stop at
submission. Published Jobs supports create_cutout only: registration of
render_timelapse in a worker does not expose it through Jobs/Swagger. The earlier
successful application-level timelapse result remains separate; BE-03 frontend
completion is not claimed.

### Confirmed exception from operator-provided Image Processing logs

The supplied running-process traceback resolves the previously hidden immediate
cause. At local times 2026-11-14 21:34:39 and 21:35:42, `CutoutRequestView.post`
failed in `ObservationApiResolver.get` -> `urllib.request.urlopen`, raising
`urllib.error.URLError` wrapping `ConnectionRefusedError: WinError 10061`.
**The public POST failed during Observation product resolution, before Jobs
submission or authentication.** The generic response `Job service unavailable`
therefore misidentifies the dependency for this failure.

A separate 21:35:44 traceback confirms the job-status proxy also failed with
WinError 10061 in `JobApiReader.get`. These are internal HTTP connectivity failures;
the target URLs are not printed in either traceback. Exact endpoint values remain
unverified, so port 8000 is not asserted as the observed destination. The direct
401 diagnostic-token probe is a separate authentication problem; it did not cause
the POST failure shown in these logs.

See [traceback summary](evidence/BE02_http_validation/traceback_summary.json).
Set the correct URLs in the terminal that launches Image Processing, then restart
that API under the operator's control. Changing variables in another terminal does
not update the running process. Before launch, the same terminal can verify only
non-sensitive adapter settings:

```cmd
set "SERVICE_NAME=image_processing"
set "OBSERVATION_SERVICE_URL=http://127.0.0.1:8001"
set "JOB_SERVICE_URL=http://127.0.0.1:8004"
set "REDIS_URL=redis://127.0.0.1:6379/0"
set "S3_ENDPOINT_URL=http://127.0.0.1:9000"
python -B -c "import os; from services.image_processing.infrastructure.clients import ObservationApiResolver, JobApiReader; print('Observation:', ObservationApiResolver().base_url); print('Jobs:', JobApiReader().base_url); print('Redis:', os.environ.get('REDIS_URL'))"
python -B manage.py runserver 127.0.0.1:8002 --noreload
```

These are proposed operator commands, not commands executed by this validation.
Existing database/S3 credentials must remain loaded, and EON_INTERNAL_TOKEN must
match Jobs without printing it. The worker needs its own correct Observation/S3/
Redis environment. No running process or configuration file was modified.
HTTP cutout acceptance remains false pending configuration confirmation and a
successful real retry.

### Latest retry after reported configuration correction

**http_cutout_flow_passed=false.** The actual retry stopped earlier than the previous
submission failure: `GET http://127.0.0.1:8002/health/` raised
`requests.ConnectionError` with connection refusal (WinError 10061). A separate
bounded recheck reproduced the refusal; no HTTP response was received from Image
Processing. The reported restart does not establish a reachable listener on :8002.

PostgreSQL SELECT 1 and Redis PING passed. Observation :8001, Jobs :8004 and MinIO
:9000 health rechecks each returned HTTP 200. Authentication, Celery worker inspection,
fresh discovery, submission, processing, new storage and FITS/metadata retrieval
were not reached in this attempt. No jobs were submitted and no FITS downloaded.
This is a local service-listener/connectivity failure; no new application defect
or authentication outcome is inferred. The older diagnostics in this directory
remain historical; they are not results from this retry.

Executed:

```cmd
.venv\Scripts\python.exe -B -u -m scripts.validate_be02_http
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp C:\Users\Angle\AppData\Local\Temp\eon-be02-http-corrected-tests --tb=short
.venv\Scripts\python.exe -B -m unittest discover -s tests -p smoke_stdlib.py
```

A supplementary requests health probe used connection/read timeouts (3,5) seconds,
once per endpoint, with proxies disabled. Results are saved in
[connectivity_recheck.json](evidence/BE02_http_validation/connectivity_recheck.json);
[validation.json](evidence/BE02_http_validation/validation.json) records the current
failed stage. Regression results: **142 passed, 6 optional skipped**; five smoke
checks passed. These regressions do not establish real HTTP cutout success.

The operator must restore Image Processing listening on 127.0.0.1:8002 before
another real attempt. No running service, .env, compose.yaml, Observation, Jobs or
application code was changed. No commit or push. Previous successful application
 timelapse validation remains separate; no public timelapse/BE-03 completion claim.

## 17. Accepted real HTTP cutout workflow after autonomous recovery

**http_cutout_flow_passed=true** for this bounded real HTTP run. This is separate
from the five-frame application timelapse acceptance. The public Jobs API still
supports `eon.image_processing.create_cutout` only; no public render_timelapse or
BE-03 frontend completion is claimed. Earlier section 16 failures remain historical.

### Recovery and lifecycle

Verified branch: `feature/EON-BE-02`. Read AGENTS.md/settings/adapters before recovery.
Listener/container inspection found PostgreSQL, Redis, MinIO, Observation :8001 and
Jobs :8004 available, but no listener on :8002. Jobs authentication with the existing
configured token succeeded: empty payload returned 400 after authentication.

Started an owned Image Processing process on :8002 with the existing .env secrets
loaded privately into memory and localhost endpoint overrides. No file or existing
service was reconfigured. The first actual HTTP submission returned 202 and ran on
the existing worker, but failed with `django.db.utils.OperationalError:
fe_sendauth: no password supplied` during repository lookup, before FITS acquisition.
That failed job is `14dafacd-da86-447f-81a4-0353a06e759f`; its evidence is retained
under `attempt_worker_missing_password/`.

After confirming that worker had no active/reserved/scheduled work, temporarily
removed its image_processing queue subscription. Started an owned worker using
`--pool=solo --concurrency=1 -Q image_processing`, correct PostgreSQL credentials,
Observation :8001 and MinIO :9000. Retried through the public API; Jobs legitimately
issued a new task ID because the previous idempotent job had failed. No Redis lock,
result record, database row or stored artifact was manually removed or fabricated.

Both owned API/worker processes were stopped after validation. The original worker's
queue subscription was restored and acknowledged. An additional owned API used for
error checks was also stopped. Observation, Jobs and infrastructure containers were
not restarted. The original worker still requires its own launch environment to
include PostgreSQL credentials; restoring its subscription does not repair that
process's environment. Port 8002 is intentionally no longer served by the temporary
validation process after cleanup. Use the recovery helper for a reproducible test,
or launch persistent development processes with the same settings.

### Real request, lifecycle and FITS result

| Stage | Result |
| --- | --- |
| PostgreSQL SELECT 1, Redis PING | Passed |
| Observation, Image Processing, Jobs, MinIO health | Passed, all HTTP 200 |
| eon-science bucket access | Passed |
| Correct create_cutout worker/queue | Passed: eon-be02-validation worker |
| Bounded M101/D2 discovery limit=1 and detail | Passed, both HTTP 200 |
| POST /api/v1/cutouts/ | Passed, HTTP 202 |
| Jobs -> Celery asynchronous execution | Passed: running -> succeeded; four polls, 240-second bound |
| Metadata endpoint | Passed, HTTP 200 |
| FITS download endpoint | Passed, HTTP 200, application/fits |
| Scientific metadata/content | Passed, actual FITS headers/WCS/finite image and source identity checked |
| MinIO persistence/retrieval | Passed, identical 4,000,320-byte object |
| Successful request/response schema validation | Passed; documented error-schema omissions below |
| Seven safe HTTP error/authentication checks | Passed |

Genuine product:
`803628b15eeaa57e572cf8598be71893281021f9dfb44b3574d7b0af3e89bd03`.
Region: RA=210.80227, Dec=54.34895, size=0.1 degrees, SPHEREx-D2. Discovery is a
fresh bounded endpoint request; neither cache miss nor native TAP success is claimed.

Successful job: `c19f23f7-5c6b-4528-b59f-f5ddcbac871e`.
Actual cutout: `65c1bfff2cef7949e4173bf988abf732a217e973026a6aa6396ba46406982a5d`.
Object: `eon-science/cutouts/65c1bfff2cef7949e4173bf988abf732a217e973026a6aa6396ba46406982a5d.fits`.
Worker logs show receipt and successful completion of that task in 8.327 seconds.
S3 LastModified is `2026-11-15T03:50:02+00:00`, during this execution.

The real downloaded FITS validates with Astropy and contains IMAGE shape 59x59,
detector 2, OBSID `2025W19_2B_0346_1`, surface-brightness unit MJy/sr, celestial WCS
and finite science pixels. FITS DATE-OBS/TIMESYS yield `2025-05-10T17:04:37.741Z`,
consistent with the original catalogue MJD within one second. FITS DATE is not used
as observation time. Header OBSID/detector match the discovered artifact filename.
The requested sky position roundtrips through the real WCS. Position-dependent
wavelength=1.5064212841578182 um and bandwidth=0.036952739361587945 um match metadata.
The metadata endpoint's returned scientific dictionary equals an independent read
of the downloaded FITS. S3 direct retrieval equals the HTTP download byte-for-byte:
SHA-256 `0143817ceec1a56d8ec85b7f8f5d760e20b3376a7afd73bf637657ff36eb980b`.
No timestamps, observation IDs or cutout IDs were invented.

### Contracts, errors and limitations

Actual request, 202 response, all status responses and metadata validate against the
published Image Processing OpenAPI 3.0.3 snapshot. FITS response Content-Type matches
its application/fits declaration. `result` and `fits` remain generic dictionaries;
scientific/identity assertions are checked separately. The schema omits observed
400/404 responses on several operations; these documentation gaps are listed in
validation.json. The mismatched-region error still mentions TAP although discovery
may use SIA2. No endpoint or agreed BE-03 manifest contract was changed.

Error checks submitted only deliberately invalid/non-enqueuing payloads:
missing fields 400; mismatched region 400; unknown job 404; invalid cutout ID 400;
unknown cutout 404; unauthenticated Jobs 401; matching-token invalid Jobs payload 400.
No extra valid jobs or FITS acquisitions were needed for these checks. The API's
broad dependency error message can still mask Observation failures, as earlier
tracebacks demonstrated. No production correction was needed to pass this run.
The existing SIP diagnostic is retained; no quality-mask calibration, photometry,
full-band equivalence or external astrometric accuracy is inferred.

### Executed commands and evidence

From the repository root, existing venv:

```cmd
.venv\Scripts\python.exe -B -u -m scripts.recover_be02_http
.venv\Scripts\python.exe -B -u -m scripts.recover_be02_http --isolate-worker
.venv\Scripts\python.exe -B -u -m scripts.recover_be02_http --error-checks-only
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp C:\Users\Angle\AppData\Local\Temp\eon-be02-recovery-tests --tb=short
.venv\Scripts\python.exe -B -m unittest discover -s tests -p smoke_stdlib.py
.venv\Scripts\python.exe -B -m ruff check --no-cache scripts/recover_be02_http.py scripts/validate_be02_http.py
```

Regression: **142 passed, 6 optional integrations skipped**. Five smoke checks and
focused lint passed. Deterministic tests using synthetic/mocked ports are distinct
from the actual HTTP/Jobs/Celery/IRSA/S3 evidence above; skipped tests are not passes.

[Current validation](evidence/BE02_http_validation/validation.json),
[submission](evidence/BE02_http_validation/submission.json),
[polls](evidence/BE02_http_validation/polls.json),
[metadata](evidence/BE02_http_validation/metadata.json),
[successful lifecycle/log summaries](evidence/BE02_http_validation/successful_pipeline_lifecycle.json),
[error checks](evidence/BE02_http_validation/error_checks.json),
[S3 metadata](evidence/BE02_http_validation/object_head.json) and
[commands](evidence/BE02_http_validation/commands.txt) record actual outcomes without
credentials. Source FITS remains in MinIO; unnecessary local FITS copies were not
retained. No .env, compose.yaml, Observation, Jobs or production application code was
changed. No commit, push or merge. Remaining launch configuration is explicit above;
no human intervention was required to complete this bounded validation.
