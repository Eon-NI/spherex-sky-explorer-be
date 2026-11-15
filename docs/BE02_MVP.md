# EON-BE-02: FITS Processing and Timelapse MVP

## 1. Executive summary

BE-02 now provides a bounded processing use case and Celery adapter for:

**Observation references -> authorized IRSA cutouts -> FITS/WCS -> chronological
PNG frames -> S3 objects -> result manifest -> BE-03.**

The implementation preserves the agreed manifest fields and introduces no job
lifecycle endpoint. Production-path references are always resolved through BE-01
HTTP. Real-data preview evidence demonstrates five PNGs and one fresh IRSA cutout;
it does not demonstrate the complete service chain. In the latest follow-up,
Observation and MinIO health endpoints both returned HTTP 200 and the configured
eon-science bucket passed real upload/retrieval checks. However, Observation's
TAP-backed temporal discovery returned HTTP 502 twice, preventing valid product IDs
from being obtained. **End-to-end acceptance remains blocked at temporal discovery;
S3 connectivity is no longer the blocker.**

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

### Follow-up integration validation: November 14, 2026 (America/Managua)

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

## 13. Known limitations and acceptance checklist

| Acceptance criterion | Evidence/status |
| --- | --- |
| Resolve a real product through BE-01 | Blocked: health is 200, but temporal discovery returned 502 twice; no real IDs obtained |
| Acquire at least one real SPHEREx cutout | Passed: one fresh bounded IRSA download in preview |
| Generate PNG from real data | Passed: five real previews |
| Generate 5-10 chronological real frames | Five historical real previews verified; current BE-01 pipeline not run because discovery failed |
| Upload frames to configured bucket | Storage adapter passed for five real preview PNGs in eon-science; new processing outputs still blocked by discovery |
| Produce correct agreed manifest | Five focused deterministic checks passed in follow-up; actual processing manifest generation/retrieval not reached |
| BE-03 consumes agreed fields unchanged | Required fields preserved/tested; task allowlist/lifecycle integration belongs to BE-03 |
| Bound resources and handle failure | Automated tests cover these guards |
| Automated suite passes | Previous full suite: 132 passed/6 skipped; follow-up: five focused deterministic checks passed |
| Document reproducible pipeline | This document, command, preview script and live test |

No full end-to-end acceptance is claimed. No calibrated photometry/quality mask,
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
spectral/coverage limitations. MinIO and bucket access are now verified. Restore
BE-01 TAP discovery or supply genuine stored IDs, then run the complete opt-in test
before declaring the hackathon MVP done.

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
