# EON MVP integration: real temporal images and preserved Spectral

Both repositories use feature/eon-mvp-integration. Backend started clean at develop
92d1ecc, including the merged Celery/Compose fixes; frontend started clean from
development. No branch switches, commits, pulls, merges or resets were performed.
No frontend AGENTS.md exists; backend instructions were read before changes.

## Spectral baseline and compatibility

Spectral was inspected/tested before temporal changes. Its frontend already uses
real /spectral/cubes and /spectral/spectra requests independently of temporal mocks.
Initial channels returned 200 with 102 actual wavelengths and blank indices 50/51.
Initial cube submission returned 502 and origin localhost:8080 had no CORS grant.
Running containers had incorrect loopback internal URLs. This baseline did not
pass; it was not silently represented as an already working browser integration.

No file under services/spectral or the frontend Spectral component directory was
modified. The Spectral API-client functions remain byte-identical. Only necessary
shared Compose/CORS/Jobs infrastructure changed. Reused patterns are a public
submission facade, idempotent Jobs dispatch, bounded status polling, completed-state
mapping, explicit loading/errors and HTTP-delivered images. No HiPS/science logic
was copied into Image Processing or Jobs.

Final real Spectral verification passed after temporal features: official DR1 tile
677 (320,909,760 bytes, existing 400 MiB cap) was prepared once by Celery job
87b9ea3b-f730-4161-a03e-2abc500f9eb0 in 160.17 s. The unchanged UI loaded its PNG,
selected the existing center-spectrum control through a trusted browser click and
rendered a real 102-point spectrum. Indices 50/51 retained null flux; unit MJy/sr,
raw NHITS/FLAGS remained intact. Repeated checks reused the completed cube/cache.
The browser test uses a screenshot for Spectral rather than requiring canvas pixel
export, which its existing cross-origin image policy does not support. No Spectral
export functionality was added or changed.

## Backend changes and service boundaries

- Compose uses explicit container endpoints: postgres, redis:6379/0, minio:9000,
  observation:8000 and jobs:8000. Real .env secrets were not changed. Only affected
  EON app containers were rebuilt/recreated in eon-stack-test; PostgreSQL, Redis,
  MinIO and independent local containers were not recreated or stopped.
- The existing Observation and Image Processing migrations were unapplied in these
  Compose databases. Applied them with migrate --noinput to their own databases;
  no migration/model files or existing scientific data were changed/deleted.
- Public API CORS allows explicit local 5173/8080 origins. EON_BROWSER_ORIGINS can
  override; legacy EON_SPECTRAL_BROWSER_ORIGINS remains supported. Internal /jobs/
  submission is not browser-enabled and its token is never sent to the frontend.
- New Jobs public facade orchestrates the existing BE-02 render_timelapse task.
  Jobs owns UUIDs, task dispatch, idempotency/status and PNG delivery. It resolves
  references over Observation HTTP; it does not access Observation/Image databases,
  download FITS or implement processing algorithms.
- Existing required BE-02 result stays job_id/status/frames; frame fields stay
  index/product_id/timestamp/object_key. Browser delivery URLs are a separate
  envelope field, not a modification of the stored manifest.
- PNG streaming is allowed only for completed timelapse jobs with the server-owned
  task marker and matching job-scoped frame prefix. Client-supplied URLs/object keys
  and caller-chosen job IDs are not accepted. Existing S3 variables/contracts remain.
- IRSA returned intermittent proxy 502s during cold discovery. A fresh bounded
  session is now closed per collection/attempt, so failed pooled transports are not
  retained for retries. Both attempts remain bounded; all four collections, strict
  scientific/VOTable checks and rejection of partial catalogues remain unchanged.
  Native TAP success is not claimed; the successful run used SIA2 fallback.

## Public temporal contract

Jobs port 8004:

```http
POST /api/v1/timelapses/
Content-Type: application/json
```

```json
{
  "product_ids": ["<genuine BE-01 product_id>"],
  "ra_deg": 210.80227,
  "dec_deg": 54.34895,
  "size_deg": 0.1,
  "detector": "SPHEREx-D2"
}
```

Accepts 1-5 references, one detector, finite coordinates and square size <=0.2 deg.
Every reference must match the geometry/detector before enqueueing. POST returns
202 with job_id/status/status_url. GET /api/v1/timelapses/{job_id}/ returns
pending/running/completed/failed, progress/error, unchanged result and delivery
[{index,url}]. GET /api/v1/timelapses/{job_id}/frames/{index}/ streams image/png.
Generic protected /api/v1/jobs/ still retains its original cutout/Spectral allowlist;
the new public facade chooses the temporal task server-side. No browser secret.

Frontend sees localhost:8001 and localhost:8004, never minio:9000. Delivery is through
Jobs using its private S3 credentials, so no presigned/internal S3 host is exposed.
Frames remain available while the existing Jobs Redis record/result is retained;
permanent/shareable delivery and production authentication are not implemented here.

## Real integration results

Search Image/Blink share an idempotent two-frame job:
c9585bf7-f7a6-4c17-b34e-1ea7a7fcd961. Image displays epoch A; Blink uses two genuinely
different FITS acquisition times. Time controls now show explicit UTC labels.

Five-frame Timelapse job: a18d40b0-08fc-449f-ba31-7f47b71a48a3. Actual UTC times:

- 2025-05-10T17:04:37.741Z
- 2025-05-24T21:58:59.879Z
- 2025-12-09T07:20:11.647Z
- 2026-05-22T19:42:28.021Z
- 2026-06-24T03:19:00.063Z

BE-01 returned 84 real references for M101/D2, size 0.1 deg, limit 100. Browser
requests performed genuine discovery, public POST 202, status 200 and PNG GET 200.
The initial five-frame job was observed processing before completion; repeat
browser runs reused the same job ID and stored data rather than repeating FITS.

All source FITS objects were HEAD-checked within the 8 MiB bound. One actual stored
FITS was independently downloaded from S3 and checked with Astropy against OBSID,
detector, checksum and DATE-OBS/TIMESYS acquisition timestamp. Every result product
resolved through BE-01. All delivered PNGs are valid 512x560 images and byte-identical
to MinIO objects. The stored manifest equals the returned raw result. Both jobs use
one 59x59 celestial grid and shared normalization within their sequence. External
astrometric accuracy, PSF matching, calibrated masks/photometry and visible motion
are not established by these checks.

Real Edge browser tests verified Image rendering, Blink canvas alternation and
five-frame playback through changing canvas hashes, using CORS-readable PNGs.
Blink prevents identical A/B selections and disables one-epoch alternation. The
confirmed Timelapse tainted-canvas issue was fixed only in its image preloader by
requesting anonymous CORS; Spectral image loading was left unchanged. Error/loading
states use actual backend failures/polling; no synthetic fallback is used.

Evidence: [artifact audit](evidence/mvp_integration/artifact_audit.json), and
browser-search.json/browser-blink.json/browser-timelapse.json/browser-spectral.json
in that directory. Browser/body/network evidence contains no credentials.

## Checks and reproduction

```cmd
docker compose -p eon-stack-test config --quiet
docker compose -p eon-stack-test build observation image_processing spectral jobs observation_worker image_worker spectral_worker observation_beat
docker compose -p eon-stack-test up -d --no-deps observation image_processing spectral jobs observation_worker image_worker spectral_worker observation_beat
docker compose -p eon-stack-test exec -T observation python -B manage.py migrate --noinput
docker compose -p eon-stack-test exec -T image_processing python -B manage.py migrate --noinput
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_mvp_orchestration.py tests/test_temporal_fallback.py
.venv\Scripts\python.exe -B -m unittest discover -s tests -p smoke_stdlib.py
.venv\Scripts\python.exe -B -u -m scripts.browser_mvp search --save
.venv\Scripts\python.exe -B -u -m scripts.browser_mvp blink --save
.venv\Scripts\python.exe -B -u -m scripts.browser_mvp timelapse --save
.venv\Scripts\python.exe -B -u -m scripts.browser_mvp spectral --save
.venv\Scripts\python.exe -B -u -m scripts.audit_mvp_results
```

The browser helper uses installed Edge/CDP and Python standard library, not a new
browser package. It starts only an owned temporary browser profile, with bounded
polling, and closes it. API calls have bounded timeouts; temporal client polling
is eight minutes and each fetch has a 90-second abort bound. Helpers are integration
tools, not additional services. audit_mvp_results is tied to the recorded job IDs.

Final tests: **171 passed, 6 optional integrations skipped** in a disposable Linux
container with source mounted read-only, pytest/pytest-django installed only in its
user site and SERVICE_NAME=observation. Windows full-suite collection remains
limited by the existing Spectral fcntl import; no portability/science refactor was
made. Windows focused tests: 24 passed; smoke: five passed. Jobs Django check and
OpenAPI validate/fail-on-warn passed; focused Ruff and git diff --check passed.
Frontend production build includes tsc -b plus Vite, and five Node contract tests
passed with existing TypeScript tooling. Unit/mocked results are distinct from the
real HTTP/Celery/FITS/S3/browser results above. Skips are not integration passes.

## Scope and remaining limits

Search prepares the two endpoint epochs; Timelapse selects up to five distributed
unique catalogue times from a bounded chronological prefix of 100 references.
Date filtering is client-side on catalogue MJD; final frame timestamps come from
FITS. A catalogue reaching 100 may omit later epochs. The verified M101 result had
84 entries. D2 was live-tested; other supported detector inputs were not live-tested.
IRSA remains an external availability dependency; its errors are surfaced, not
replaced by mocks. Temporal click tools are not calibrated astrometric/photometric
measurements. Existing FLAGS are retained, not interpreted as calibrated masks.
Different exposure wavelengths can change apparent brightness, so no variability
or motion inference is made. Difference/Stack are excluded from the active MVP UI.

No volumes/scientific objects were deleted, no .env secrets changed, no unrelated
project was inspected/modified, and no commit/push/merge occurred. Both repositories
remain independently on feature/eon-mvp-integration with uncommitted changes.
