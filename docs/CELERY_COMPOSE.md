# Compose Celery workers and Observation Beat

## Root causes and correction

Spectral requires a worker: Jobs routes `eon.spectral.prepare_cube` and
`eon.spectral.extract_spectrum` to `spectral`. The Celery app already autodiscovers
those tasks with SERVICE_NAME=spectral. Existing workers consume celery and
image_processing, so neither could consume Spectral tasks. This was missing
deployment configuration, not scientific/business logic.

Added spectral_worker with SERVICE_NAME=spectral, `-Q spectral`, explicit Linux
prefork pool and concurrency two. It waits for the existing PostgreSQL/Redis and
successful MinIO setup, like image_worker. Existing Spectral prefetch one, late
acknowledgments and task limits remain unchanged. Two processes let another request
run during a cold download; memory/network sizing remains an operator concern.
Existing concurrency stays Observation=1, Image Processing=2. Windows solo is a
separate host-development setting, not needed for Linux containers.

Beat logs showed `PermissionError: [Errno 13] Permission denied:
'celerybeat-schedule'` in shelve/dbm state creation. The default relative filename
resolved under root-owned /app, unwritable by eon UID 1000. This was a filesystem
startup defect, not IRSA/Redis failure. A corrupted-schedule recovery frame in the
traceback does not establish that a previously corrupted schedule existed.

Dockerfile now creates /home/eon/beat owned by eon before USER eon. Compose mounts
a new beatdata volume there and passes
`--schedule=/home/eon/beat/celerybeat-schedule`. Docker initializes a new volume
from that owned image directory. Beat remains non-root with PersistentScheduler.
The daily task and 86400-second cadence are unchanged. No old volume/data was
deleted or recursively reowned. Existing volumes with wrong permissions would
require separate operator investigation, not blanket chmod/chown.

## Startup commands

Use the existing eon-stack-test project/environment; its MinIO publishes 19000/19001
and does not replace the independent MinIO on 9000/9001. Do not print resolved
Compose configuration containing secrets.

```cmd
docker compose -p eon-stack-test config --quiet
docker compose -p eon-stack-test build observation_beat spectral_worker
docker compose -p eon-stack-test up -d --no-deps spectral_worker observation_beat
docker compose -p eon-stack-test exec -T spectral_worker celery -A eon_common.celery inspect active_queues --timeout=5
docker compose -p eon-stack-test exec -T spectral_worker celery -A eon_common.celery inspect registered --timeout=5
```

--no-deps is appropriate here because dependencies were already running/verified.
For a fresh deployment, start normal Compose dependencies. Run one Beat per
broker/schedule; do not scale its replicas. beatdata is separate from science data.

## Real verification on November 15, 2026

Executed with the existing venv:

```cmd
.venv\Scripts\python.exe -B -u -m scripts.validate_celery_compose
```

[Evidence](evidence/celery_compose/validation.json) records real broker/HTTP checks:

- Compose config and both affected image builds passed.
- Spectral queue was empty before startup. Real inspection confirmed both task
  registrations, spectral queue, Linux prefork pool and concurrency two.
- A small celery.accumulate probe explicitly routed to spectral returned SUCCESS
  and its exact marker through Redis. Both actual Spectral task names were also
  dispatched with intentionally missing required arguments: each was consumed and
  failed with expected TypeError before the function body. This verifies routing
  and registration, not successful scientific computation. No cube was downloaded.
- Existing Observation and Image Processing workers responded on their unchanged
  queues and each independently completed a real celery.accumulate probe.
- Beat ran with RestartCount=0 during a 60.38-second observation and later checks,
  with unchanged ID/start time. Its startup log identifies PersistentScheduler and
  the new path. Nonempty schedule/SQLite WAL files exist, writable by UID 1000.
  Settings still contain the same sync-irsa task and daily interval.
- All four API schemas :8001-8004 and MinIO :19000 health returned HTTP 200.
- Existing stack APIs, workers and infrastructure and all three independent local
  containers retained identical IDs/start times/restart counts. Only broken Beat
  was recreated; Spectral worker was added. Both repaired services remain running.
  No containers were brought down and no volume was deleted.

The 24-hour sync was not forced or awaited. No cold-tile science job or long-term
scheduler soak was run. Schedule state exists on disk, but deliberate recreation
persistence was not additionally exercised. Its named volume supplies persistence.

## Regression and platform checks

```cmd
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp C:\Users\Angle\AppData\Local\Temp\eon-celery-compose-tests --tb=short
.venv\Scripts\python.exe -B -m unittest discover -s tests -p smoke_stdlib.py
.venv\Scripts\python.exe -B -m ruff check --no-cache scripts/validate_celery_compose.py
```

Windows full-suite collection was blocked by the pre-existing Unix-only fcntl
import in Spectral. No portability/algorithm changes were made. All five Windows
smoke checks passed. Full regression ran in a disposable Linux container from the
built Spectral image, with repository sources mounted read-only. pytest and
pytest-django were installed only into that temporary container's user site.
Docker command (Python program supplied on stdin):

```cmd
docker run --rm -i --name eon-celery-regression --mount "type=bind,source=C:\Users\Angle\Documents\Proyectos\spherex-sky-explorer-be,target=/workspace,readonly" --workdir /workspace --env SERVICE_NAME=observation --env PYTHONDONTWRITEBYTECODE=1 --entrypoint python eon-stack-test-spectral_worker -
```

Exact Python program sent to that command:

```python
import subprocess,sys
subprocess.run([sys.executable,'-m','pip','install','--user','--quiet','pytest>=8,<10','pytest-django>=4.10,<5'],check=True)
sys.exit(subprocess.run([sys.executable,'-B','-m','pytest','-q','-p','no:cacheprovider','--basetemp','/tmp/eon-celery-tests','--tb=short']).returncode)
```

Result: **157 passed, 6 optional integrations skipped**, in 8.27 seconds. These
synthetic/mocked regressions are separate from live broker checks. Focused lint
and git diff --check passed. Two preliminary disposable invocations hit Windows
shell quoting/CRLF issues before tests ran; Python stdin resolved them. No test-tool
installation affected running services.

## Files and remaining scope

- compose.yaml: required Spectral worker; Beat schedule volume/path.
- Dockerfile: non-root-owned Beat directory.
- scripts/validate_celery_compose.py: bounded actual deployment checks/evidence.
- docs/CELERY_COMPOSE.md and README.md: root cause/startup/verification/limits.
- AGENTS.md: correct its outdated missing-Spectral-worker statement.
- docs/evidence/celery_compose/validation.json: actual outcomes and snapshots.

No .env, business logic, scientific algorithms, task contracts or existing queues
changed. No commit/push/merge. Changes remain uncommitted on
fix/eon-celery-compose. No blocker remains for the targeted Linux Compose fix;
Windows full-suite portability and full scientific E2E are outside this change.
