# Compose MinIO: non-root local storage

The optional Compose server uses `alpine/minio:RELEASE.2025-10-15T17-29-55Z`.
This is a **community-maintained image**, not the official minio/minio image and not
an Alpine Linux endorsement. Publisher/source:
[alpine-docker/minio](https://github.com/alpine-docker/minio) and
[Docker Hub](https://hub.docker.com/r/alpine/minio/).
The local image was inspected, not assumed operational: default user `minio`, UID
100, GID 101; wget/chown/stat are present. Observed repository digest:
`sha256:cf23643a6cf9ce159c57643ceb88279e431262282428c9e0bf3a7ef1a97e84b4`.
The release tag is pinned; project Python dependencies retain their existing ranges,
so the whole application build is not a fully locked dependency snapshot.

## Ownership without a root server

`minio_volume_init` uses the same image and runs once as root solely to initialize
the root directory of a **new empty** named volume. It derives the image's minio
UID/GID and chowns `/data` to 100:101. It does not recursively chown/chmod files,
delete data or change named-volume identities. If a nonempty volume has a different
root-directory owner, it fails explicitly before touching ownership. Existing data
must be backed up and migrated separately by its operator. It is deliberately not
automatically migrated by a Compose start. An already correctly owned volume passes
without any ownership changes. A correctly owned volume with inaccessible nested
files may still fail: this initializer does not repair arbitrary existing data.

The MinIO server explicitly runs as `100:101` and depends on successful volume init.
Root is never used to run the storage server. Existing `pgdata`, `redisdata` and
`miniodata` names/mounts are preserved. No existing volume was mounted for testing.

## Bucket initialization and bounds

`minio/mc:latest` is removed. `minio_setup` builds the existing application Dockerfile
and runs `docker/init_s3.py` as its existing unprivileged eon user. The Dockerfile
only adds a COPY for that script. No SDK or framework dependency is added: boto3
already belongs to the project. The initializer imports no Django/scientific code,
uses no database and performs no FITS processing.

The server health check requests `/minio/health/ready` (3-second wget timeout,
4-second Docker timeout, 5-second interval, 12 retries, 5-second start period).
Compose waits for server health before starting bucket setup. The script also
allows at most 12 readiness attempts, with a 3-second request timeout and 2 seconds
between attempts, then exits nonzero. S3 connections/reads are bounded to 3/5 seconds
with at most two SDK attempts per operation. No infinite initialization loop or
restart policy is used for the one-shot services.

Bucket setup HEADs the configured bucket. Only HTTP 404 permits creation, followed
by another HEAD. Authentication/permission/other server errors fail. An already
owned bucket or a concurrent creation by the same owner is accepted; a foreign
bucket is not silently accepted. No objects are deleted by initialization.

The S3 contract is unchanged: S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET and
S3_ENDPOINT_URL. S3_BUCKET and secret are required; access-key fallback remains eon,
matching the server's existing fallback. Compose setup always uses
`http://minio:9000`, independently of Windows .env endpoint values. Application
adapters and business logic are unchanged. Container applications still require
container endpoints in their launch environment: postgres, redis, minio,
observation:8000 and jobs:8000. Host Windows processes use 127.0.0.1 and published
ports. No .env secrets are edited by this change.

## Collisions and isolated validation

The independent containers `eon-postgres-local`, `eon-redis-local` and
`eon-minio-local` already exist. MinIO occupies host 9000/9001; the independent
PostgreSQL/Redis occupy 5432/6379. Compose PostgreSQL/Redis have no published ports,
but launching the entire stack may conflict with independently running Django ports
8001-8004. Do not launch the whole stack as a storage-only test.

MinIO host ports are configurable via MINIO_API_PORT / MINIO_CONSOLE_PORT, with
unchanged defaults 9000/9001. The service name `minio`, container port 9000 and
internal S3 URL are unchanged regardless of host-port overrides. Compose has no
fixed container_name, so separate project names isolate container/network/volume
names. Changing a project name selects different volumes; it does not migrate data.

Reproduce the isolated real check (existing .venv with project dependencies):

```cmd
.venv\Scripts\python.exe -B -u -m scripts.validate_compose_minio
```

The helper checks test ports 19000/19001 are free, creates a unique Compose project,
uses temporary random credentials and a test bucket, builds/starts only MinIO and
its two initializers, and tests actual health, non-root writable volume, bucket
creation, S3 put/get/delete and repeated initialization. It runs bounded subprocesses.
It compares existing container IDs, start times and mounts before/after. It removes
only test containers/network with `docker compose down` (without -v), retains the
isolated volume and never removes any volume. Its generated env file is temporary;
no real .env secret is printed or changed. The empty test bucket/MinIO metadata may
remain on the retained isolated volume. Generated test image/cache are also retained.

The helper does not test the full Django/Jobs/Celery stack or IRSA. This task does
not change BE-01/02/03 business logic or the BE-03 result contract.

## Checks and evidence

Verified on branch `fix-compose`, using the existing venv and Docker Desktop:

```cmd
docker compose config --quiet
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_s3_initializer.py --basetemp C:\Users\Angle\AppData\Local\Temp\eon-compose-init-tests
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp C:\Users\Angle\AppData\Local\Temp\eon-compose-regression --tb=short
.venv\Scripts\python.exe -B -m ruff check --no-cache docker/init_s3.py tests/test_s3_initializer.py scripts/validate_compose_minio.py
.venv\Scripts\python.exe -B -u -m scripts.validate_compose_minio
git diff --check
```

- Compose syntax/configuration passed.
- Seven focused unit tests passed (existing bucket, create/verify, 403/500 rejection,
  same-owner/foreign-owner creation races, finite readiness attempts).
- Full deterministic regression suite: **149 passed, 6 optional integrations skipped**.
  Skipped live IRSA/service tests are not claimed as passes.
- Focused Ruff and whitespace checks passed.
- Real isolated build, volume initializer, non-root server health (HTTP 200),
  containerized boto3 bucket initialization, S3 upload/download byte equality,
  object deletion and repeated bucket initialization all passed.
- Negative ownership test passed: a sentinel in a fresh root-owned test volume
  caused initialization to exit nonzero; its file content and root ownership were
  unchanged. Only that generated sentinel was removed to allow the normal test.
- Original independent container IDs/start times/mounts remained exactly unchanged;
  none was stopped or restarted. All test containers/networks were removed without
  deleting volumes. Two isolated validation volumes and local build images/cache
  remain; their names are recorded in the two evidence files.

Current [evidence](evidence/compose_minio/validation.json) uses project
`eon-minio-check-90641896`, ports 19000/19001 and retained volume
`eon-minio-check-90641896_miniodata`. [First-run evidence](evidence/compose_minio/first_validation.json)
records the earlier successful run. A Windows cp1252 decoder warning during that
first build did not invalidate its S3 checks; the helper now explicitly decodes
Docker output as UTF-8 and the final rerun completed without that warning.

## Remaining limits

The test uses newly created isolated volumes and temporary credentials, not the
real standalone service's data or production credentials. Existing nonempty volumes
with other ownership deliberately require operator-managed backup/migration. No
migration was executed. The server release is community maintained and pinned, but
this task does not certify its security posture or long-term publisher availability.
No full Django/Celery/PostgreSQL Compose deployment or FITS pipeline was rerun.
The initializer reuses the application image, which is larger than a dedicated S3
utility image and requires an initial build; this avoids another client image and
another dependency version specification. No business logic, real .env secrets,
application data, existing persistent volumes or service URLs were changed. No
commit or push was performed.
