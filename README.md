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
| Observation | 8001 | SIA2/TAP, exact-query Redis/PostgreSQL cache, daily regional sync |
| Image Processing | 8002 | Verified FITS cutouts, S3, metadata/WCS and BE-02 PNG processing |
| Spectral | 8003 | Cube/extraction contracts only |
| Jobs | 8004 | Existing Celery jobs; BE-03 owns orchestration and frontend delivery |

See [BE-01 contracts](docs/BE01_MVP.md), [BE-02 MVP](docs/BE02_MVP.md),
[workflow](docs/BACKEND_WORKFLOW.md) and [IRSA constraints](docs/IRSA_INTEGRATION.md).

Rectangular catalog FOV, HiPS, difference, stack, spectral extraction and frontend
playback remain outside this implementation. BE-02 now generates PNG sequences;
it does not implement the frontend player or BE-03 job lifecycle. radius_deg is a
circular radius; fov_deg is its diameter. QR3 has default SIA2 priority, supplemented
by DR1. TAP preserves chronology. Daily sync queries configured regions across four
wide/deep collections and writes only new/changed metadata.

## Requirements and local Windows setup

Python 3.12/3.13, PostgreSQL 17 and Redis 7. Copy .env.example to .env and configure
secrets outside source control. manage.py does not load .env automatically: export
variables in CMD/PowerShell or use your development environment's loader.

```cmd
py -3.13 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -e ".[dev]"
set SERVICE_NAME=image_processing
set POSTGRES_HOST=127.0.0.1
set REDIS_URL=redis://127.0.0.1:6379/0
set OBSERVATION_SERVICE_URL=http://127.0.0.1:8001
set JOB_SERVICE_URL=http://127.0.0.1:8004
set S3_ENDPOINT_URL=http://127.0.0.1:9000
python manage.py check
```

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
compose.yaml was not changed; its historical storage topology is not the selected
independent-container setup. Do not assume the development service is running.

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
The current Jobs HTTP allowlist still supports cutouts only; BE-03 must integrate
the new task/payload. No second lifecycle endpoint was introduced.

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

## Historical Docker topology

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

The historical Compose exposes MinIO on localhost:9000 and console :9001 and uses
minio_setup to create S3_BUCKET. BE-02 does not modify or invoke this topology.

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
