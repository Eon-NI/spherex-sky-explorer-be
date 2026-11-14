# EON backend agent notes

## Context and layout

EON explores public SPHEREx spectral-image observations from IRSA. This repository is a monorepo with four deployable Django processes, selected by `SERVICE_NAME`. `eon_common` holds settings, Celery and shared HTTP wiring. `services/{observation,image_processing,spectral,jobs}` hold bounded contexts; each has its own PostgreSQL database. Docker Compose supplies PostgreSQL 17, Redis 7, MinIO and workers.

- Observation (8001): SIA2 searches, TAP chronology, daily regional Celery sync across four QR3/DR1 collections, exact-query Redis/PostgreSQL caches, product provenance and lookup. Owns the observation tables.
- Image Processing (8002): verified IRSA cutout downloads, S3 storage, FITS metadata/WCS inspection. Owns cutout provenance. Its worker consumes the `image_processing` queue.
- Jobs (8004): accepts allowlisted task submissions, idempotency in Redis, status from Celery result backend. Image Processing calls its HTTP API.
- Spectral (8003): contracts only; no 102-channel extraction API yet.

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
