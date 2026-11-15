# EON backend agent notes

## Context and layout

EON explores public SPHEREx spectral-image observations from IRSA. This repository is a monorepo with four deployable Django processes, selected by `SERVICE_NAME`. `eon_common` holds settings, Celery and shared HTTP wiring. `services/{observation,image_processing,spectral,jobs}` hold bounded contexts; each has its own PostgreSQL database. Docker Compose supplies PostgreSQL 17, Redis 7, MinIO and workers.

- Observation (8001): SIA2 searches, TAP chronology, daily regional Celery sync across four QR3/DR1 collections, exact-query Redis/PostgreSQL caches, product provenance and lookup. Owns the observation tables.
- Image Processing (8002): verified IRSA cutout downloads, S3 storage, FITS metadata/WCS inspection. Owns cutout provenance. Its worker consumes the `image_processing` queue.
- Jobs (8004): accepts allowlisted task submissions, idempotency in Redis, status from Celery result backend. Image Processing calls its HTTP API.
- Spectral (8003): official DR1 HiPS channel metadata, bounded FITS tile ingestion, WCS preview mapping, PNG layers and 102-point exploratory spectra. Its Celery worker consumes the `spectral` queue when started separately.

## Rules

Keep `domain` free of Django, Astropy, Astroquery, Celery, boto3 and HTTP clients. Use `application` for ports and cases, `infrastructure` for external adapters, and `presentation` for DRF. Do not query another service's database. Do not pass FITS bytes in JSON or Celery messages. Use the existing dependency version ranges in `pyproject.toml`; avoid adding a second job framework.

Scientific values retain their original units and provenance. SIA2 defaults to QR3 then DR1; deep collections are included in scheduled sync. Search cache entries store product IDs, not FITS, and a durable PostgreSQL generation invalidates Redis keys when metadata changes. SIA2 `radius_deg` is a circular search radius, while TAP uses a point containment predicate and `size_deg` for a square cutout. `time_bounds_lower` orders observations; FITS `DATE` is header metadata and is not substituted for observation time. `WCS-WAVE` is position dependent; do not assign one constant wavelength to an IMAGE. FLAGS bit names are returned as raw header labels, without guessed meaning. Official QR3/DR1 collections supersede QR2.

The Observation SIA adapter uses direct GET requests to IRSA's `/SIA` with one `COLLECTION` per request, matching the verified local `SPHERExProofOfConcept/spherex_poc/archive.py`. It validates VOTable `QUERY_STATUS` and time units. The direct request avoids `/SIA/capabilities` discovery; keep TAP as a separate adapter. A prior live PoC result is evidence for that PoC run, not proof that IRSA is reachable in the current environment.

## Development

Copy `.env.example` to `.env`, set secrets, then `docker compose up --build -d` and run migrations for Observation and Image Processing. The MinIO setup service creates the bucket. API schema and docs are at `/api/v1/schema/` and `/api/v1/docs/` per service. Run `pytest -q` for unit tests and `EON_RUN_LIVE_IRSA=1 pytest -q tests/integration -m integration` for live network tests. See `docs/IRSA_INTEGRATION.md` and `docs/BACKEND_WORKFLOW.md`.

Do not claim an integration test passed unless it actually ran against IRSA and, for storage tests, MinIO. Network and Docker access may be unavailable in some agent environments; report that plainly. Do not commit or push unless requested.

## Process A and B status

Process A is implemented for circular SIA2 FOV (diameter `fov_deg` or radius `radius_deg`) and point-based TAP cutout searches. Search checks Redis and PostgreSQL snapshots before IRSA; cache TTL is configurable. Image cutouts are queued through Job Service and verified against Observation's TAP-discovered product record, streamed into bounded temporary files and stored in S3. FITS metadata are inspected with Astropy. The rectangular FOV query remains unimplemented.

Process B runs daily through Celery Beat. Configure up to 20 regions using `IRSA_SYNC_REGIONS` JSON or the legacy single-region variables. Each region queries QR3, QR3 Deep, DR1 and DR1 Deep; only changed/new metadata are bulk written, then the durable cache generation is incremented. No FITS is downloaded during sync. This does not imply full-sky synchronization.

Rate limits use DRF scoped throttles with Redis; Observation sync concurrency is 1 and Image worker concurrency is 2. The Job submission endpoint requires `EON_INTERNAL_TOKEN`. Do not bypass product URL checks or enable HTTP redirects for IRSA downloads. Verification status and environment limits are in README.

## EON-BE-03 spectral exploration

Spectral uses only the official DR1 HiPS cube and the documented order-5 FITS tile path. Read `properties` for the 102 actual display wavelengths (`um`); indices 50 and 51 are blank. The worker stores the original tile and result JSON in S3. FITS `IMAGE` retains its `BUNIT`, `NHITS` is contributing pixel count, and `FLAGS` stays a raw bitmask. Evaluate `WCS-WAVE` with the full HDU list at the selected position; a global display wavelength is not a replacement for position-dependent wavelength. A single-pixel spectrum is for visualization only. Preview clicks must be converted with the tile WCS before requesting a spectrum.

Jobs accepts two additional allowlisted Spectral tasks on the `spectral` queue and continues to own job IDs, idempotency and progress. Spectral maps legacy Jobs `succeeded` to public `completed`; Image Processing's existing contract is unchanged. `compose.yaml` still has no Spectral worker: run one explicitly when deploying this feature. See [docs/SPECTRAL_EXPLORATION.md](docs/SPECTRAL_EXPLORATION.md) for JSON contracts and verification notes.

EON-BE-03 verification on 2026-11-15 used the official 320,909,760-byte DR1 tile, a temporary official MinIO server, Redis/Celery/Jobs and a headless Chrome frontend click. Cube and spectrum jobs reached `completed`, previews and 102 points rendered, and indices 50/51 retained null flux. Unit regression (4 tests, including interrupted-download resume), Django check and frontend `pnpm build` passed. A separate browser cache-miss run for another tile hit an IRSA read timeout; the adapter now resumes with validated HTTP Range, but that specific live retry has not been repeated. `compose.yaml`'s pinned MinIO images could not be pulled from this registry; the successful storage test used a checksum-verified official MinIO release binary started separately. See the detailed request/response evidence in the spectral document. Do not infer a successful clean Compose start from this test setup.

On 2026-11-14 the local `tck-03` branch fast-forwarded to `origin/develop` at `634044b`, adding Observation's bounded SIA2 temporal fallback. Observation was restarted with a source mount and its temporal endpoint returned HTTP 200 with a DR1 product. The original cold-tile job eventually completed; a worker log measured 419 seconds. The frontend displays Jobs progress and honors `Retry-After` on HTTP 429; `pnpm build` passed.

Subsequent investigation found that Jobs incorrectly applied Image Processing's 10/min submission throttle to Spectral, causing Jobs 429 to surface as Spectral 502. Jobs now chooses a separate 30/min Spectral scope, and Spectral propagates upstream 429. Preview scope is 120/min. A bounded local disk cache avoids repeated 321 MB S3 downloads for spectra and previews, and a maximum of four verified IRSA HTTP byte ranges is used for cold downloads. A first live parallel attempt failed because Celery progress was updated from a transfer thread; progress is now emitted only by the task thread. The retry completed in 381.35 s for tile 2666 with a persistent S3 key. The previous serial ingestion of a different tile took 419.14 s; this is not a controlled comparison. Cached spectra completed in 0.03–0.3 s of worker time, and two preview requests returned 200 in 0.95 s and 0.092 s. Spectral tasks use late acknowledgments and prefetch one; two live Spectral workers are running to prevent cold downloads from holding up every spectrum. Eighteen regressions passed. See `docs/SPECTRAL_EXPLORATION.md`.
