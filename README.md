# EON - SPHEREx Sky Explorer Backend

Backend for SPHEREx SIA2/TAP discovery, verified IRSA FITS cutouts, scientific
metadata inspection and bounded chronological PNG sequences. Spectral extraction
remains a future implementation.

## Architecture

Each Django process selects one bounded context with SERVICE_NAME and uses its own
PostgreSQL database. Domain contains framework-free types/rules; application defines
ports/use cases; infrastructure implements IRSA, PostgreSQL and S3 adapters;
presentation owns HTTP. eon_common contains shared configuration and transport.

| Service | Port | Scope |
| --- | ---: | --- |
| Observation | 8001 | SIA2/TAP discovery, Redis/PostgreSQL caching, daily regional synchronization, and bounded SIA2 temporal fallback |
| Image Processing | 8002 | Verified FITS cutouts, S3 storage, FITS/WCS metadata, and chronological PNG timelapse processing |
| Spectral | 8003 | SPHEREx DR1 HiPS cube exploration, previews, and exploratory spectral extraction |
| Jobs | 8004 | Celery jobs, Redis idempotency, job status and result retrieval; BE-03 owns orchestration and frontend delivery |

See [BE-01 contracts](docs/BE01_MVP.md), [BE-02 MVP](docs/BE02_MVP.md), [Spectral exploration](docs/SPECTRAL_EXPLORATION.md), [backend workflow](docs/BACKEND_WORKFLOW.md), and [IRSA constraints](docs/IRSA_INTEGRATION.md).

Rectangular catalog FOV, difference imaging, stacking, and frontend timelapse playback remain outside the verified implementation scope. BE-02 generates chronological PNG sequences and a BE-03-compatible manifest, but does not implement frontend playback or the complete BE-03 orchestration lifecycle.

In Observation, `radius_deg` is the radius of a circular search, while `fov_deg` is its diameter, not the side length of a rectangular region. QR3 has default SIA2 priority, supplemented by DR1. Temporal discovery supports a bounded SIA2 fallback when TAP is unavailable; native TAP queries may still time out.

Daily regional synchronization queries configured regions across four wide/deep collections and updates only new or changed metadata.

Spectral exploration and extraction are documented in [docs/SPECTRAL_EXPLORATION.md](docs/SPECTRAL_EXPLORATION.md). Spectral processing requires a Celery worker subscribed to the `spectral` queue. Verify that the final Docker Compose configuration includes this worker before claiming complete deployment support.

## Requirements and local Windows setup

Use Docker Engine with Docker Compose, or Python 3.12/3.13 with PostgreSQL 17 and Redis 7. Image processing also requires S3-compatible object storage.

Copy `.env.example` to `.env` and configure credentials outside source control. `manage.py` does not automatically load `.env`; export the required variables in CMD/PowerShell or use an environment loader.

To enable daily Observation synchronization, configure `IRSA_SYNC_REGIONS` as a JSON list of regions containing `ra_deg`, `dec_deg`, and `radius_deg`, or use the individual variables `IRSA_SYNC_RA_DEG`, `IRSA_SYNC_DEC_DEG`, and `IRSA_SYNC_RADIUS_DEG`. Without configured regions, the scheduled task skips synchronization.

The maximum SIA2 search radius is 1 degree, corresponding to a circular field of view of up to 2 degrees in diameter, subject to `EON_MAX_FOV_DEG`.

Provide DJANGO_SECRET_KEY, PostgreSQL credentials and S3_ACCESS_KEY/S3_SECRET_KEY/
S3_BUCKET through your local configuration. Use SERVICE_NAME=observation and
`python manage.py runserver 8001` for Observation, or runserver 8002 for Image API.
The BE-02 processing command and tests do not require the Image database; the
existing cutout provenance API still uses it. All processes expose /health/,
/api/v1/schema/ and /api/v1/docs/. The health endpoint is /health/, not
/api/v1/health/. A favicon.ico 404 simply means no favicon is configured.

For the hackathon MinIO uses an independent container with image
alpine/minio:RELEASE.2025-10-15T17-29-55Z. Native Windows Django uses
S3_ENDPOINT_URL=http://127.0.0.1:9000. No adapter depends on that image specifically.
See BE02_MVP.md for bucket setup, validation results and Docker commands.
The optional Compose MinIO setup uses the same pinned community image with a
non-root server, guarded empty-volume initialization and a bounded boto3 bucket
initializer. See [Compose MinIO setup](docs/COMPOSE_MINIO.md). The independent
container and Compose server must use different host ports; do not assume either
service is running.

## Observation examples

```bash
curl 'http://localhost:8001/api/v1/observations/search/?ra_deg=304.693508808&dec_deg=42.4436872991&radius_deg=0.0002777778'
```

Coordinates and fields come from the [official IRSA tutorial](https://caltech-ipac.github.io/irsa-tutorials/spherex-intro/).
SIA2 uses direct GET /SIA without capabilities discovery. The tutorial also documents
query_sia, spherex_dr1/spherex_qr3 and obs_publisher_did/obs_collection/access_url/
s_ra/s_dec. Availability changes over time. 502 means upstream failure, not no data.

Search checks Redis/PostgreSQL snapshots for EON_OBSERVATION_CACHE_TTL_SECONDS
(default six hours). A valid hit avoids IRSA. /api/v1/observations/temporal/ returns
query-associated product_id references; /temporal/{product_id}/ resolves them for
Image Processing. Observation never downloads FITS during searches or sync.

The existing POST /api/v1/cutouts/ path returns 202 and status_url; successful jobs
provide cutout_id, metadata and a separate binary FITS stream at
/api/v1/cutouts/{cutout_id}/fits/. The new BE-02 entry point is the processing task
`eon.image_processing.render_timelapse`, called by BE-03 with an existing job_id,
1-10 product IDs and matching region. It returns a manifest only after S3 uploads.
The protected Jobs HTTP allowlist retains cutout/Spectral tasks. Public temporal
orchestration is exposed through /api/v1/timelapses/; see docs/MVP_INTEGRATION.md.

## Configuration and synchronization

IRSA_TIMEOUT_SECONDS controls SIA2/TAP HTTP read timeout (1-120 seconds; connection
15 seconds). EON_TAP_MAX_RESULTS limits TAP volume (default 100, domain cap 500).
EON_MAX_CUTOUT_BYTES bounds existing cutout downloads; BE-02 independently caps each
input at 8 MiB, 512 pixels per axis and ten inputs. EON_INTERNAL_TOKEN protects
internal job submission. Rate limits use Redis. Existing worker concurrency is
one for sync and two for image processing.

Set IRSA_SYNC_REGIONS to a JSON list of at most 20 regions with ra_deg, dec_deg and
radius_deg, or use IRSA_SYNC_RA_DEG/IRSA_SYNC_DEC_DEG/IRSA_SYNC_RADIUS_DEG. Without
regions the task skips work. The maximum SIA2 radius is one degree; circular FOV
diameter is at most two degrees, optionally reduced by EON_MAX_FOV_DEG.

Beat schedules sync_irsa every 86,400 seconds. It queries spherex_qr3,
spherex_qr3_deep, spherex_dr1 and spherex_dr1_deep for each region, bulk compares
publisher DIDs and writes changed/new metadata only. A durable PostgreSQL generation
invalidates SIA2/TAP query snapshots. This is regional coverage, not a complete
spatial index or full-sky synchronization; full-sky work needs partitioning/quotas.

## Optional Docker Compose topology

These retained commands describe the repository's Compose topology, not the
hackathon's independent MinIO setup. S3_ENDPOINT_URL must be adapted when Django
runs in a container; 127.0.0.1 would refer to that container.

```bash
cp .env.example .env
# Configure credentials first.
docker compose up --build -d
for service in observation image_processing spectral jobs; do
  docker compose exec "$service" python manage.py migrate --noinput
done
```

Compose defaults to MinIO host ports 9000/9001; override MINIO_API_PORT and
MINIO_CONSOLE_PORT when the independent container is running. minio_setup uses
project boto3 dependencies to initialize S3_BUCKET. Container clients use
S3_ENDPOINT_URL=http://minio:9000; host clients use the published port. See
[permission strategy and isolated verification](docs/COMPOSE_MINIO.md).

## Development and verification

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
ruff check .
SERVICE_NAME=observation python manage.py check
```

Deterministic tests use synthetic FITS and mocked ports; MinIO is not required.
EON_RUN_LIVE_IRSA=1 enables live IRSA tests, some of which download a bounded FITS.
EON_RUN_S3=1 enables storage tests. EON_RUN_BE02_LIVE=1 and EON_BE02_REQUEST_FILE
explicitly enable the complete BE-01/IRSA/S3 sequence test. Do not interpret skipped
integrations as successes. See BE02_MVP.md for current commands/results.

Historical BE-01 verification (November 14, 2026): 79 tests and five smoke checks
passed; live SIA2 passed and the exact DR1 reference query returned 330 products.
TAP limit=5 exhausted 30/60-second read timeouts. This is not a successful live TAP
integration. Earlier local checks also recorded HTTP 200 from /health/, /api/v1/docs/
and M101 SIA2 (246 products), no outstanding Observation migrations, and 22 unit tests
passing in a container. Those counts describe individual runs, not fixed expectations.

At initial setup, compileall, a domain/application smoke test and docker compose
config --quiet passed; pytest could not run because it was unavailable and package
DNS resolution failed. An independent IRSA GET for
COLLECTION=spherex_dr1&POS=circle+127.69444+-39.17760+0.01 returned QUERY_STATUS=OK,
51 fields and 414 rows. That check validated IRSA metadata, not a running Django API.

Scientific details, source links, FITS FLAGS/WCS rules and environment limits are
maintained in AGENTS.md and docs/IRSA_INTEGRATION.md. Do not equate a previous PoC
success with current IRSA, MinIO or end-to-end service availability.


## Compose workers and scheduler

Compose includes Observation (`celery`), Image Processing (`image_processing`) and
Spectral (`spectral`) workers. Observation Beat stores its unchanged daily schedule
in a non-root writable persistent beatdata volume. See
[Celery Compose setup and evidence](docs/CELERY_COMPOSE.md) for targeted startup,
routing, actual runtime checks and platform limitations.


## Real MVP temporal frontend integration

Jobs now provides the public `/api/v1/timelapses/` facade for bounded Image/Blink and
five-frame playback, reusing BE-02 and delivering private MinIO PNGs through Jobs.
The stored BE-02 manifest is unchanged. See [MVP integration](docs/MVP_INTEGRATION.md)
for contracts, container endpoints, migrations, real browser/scientific verification,
Spectral baseline/regression and remaining limits. Internal `/jobs/` stays separate.
