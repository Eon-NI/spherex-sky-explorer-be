# EON-BE-03 contract (DR1 HiPS cube)

Source: [IRSA HiPS cube tutorial](https://caltech-ipac.github.io/irsa-tutorials/spherex-hipscube/) and [SPHEREx tutorials](https://caltech-ipac.github.io/irsa-tutorials/spherex/). The live `properties` document was checked on 2026-11-14. It reports `creator_did=ivo://IPAC/P/SPHEREx/DR1/cube`, 102 frames, wavelength unit `um`, order 5 and blank indices 50 and 51. The FITS tile path is the tutorial's `hips_20261026/Norder5/Dir{floor(ipix/10000)*10000}/Npix{ipix}_cube.fits`.

## Responsibilities and compatibility

Observation (8001) owns SIA/TAP product references and coordinate searches. The DR1 HiPS cube is a separate official visualization product, so Spectral (8003) resolves a validated sky coordinate to its order-5 tile using the documented HiPS resource; it does not insert a synthetic Observation row or read Observation's database. Spectral owns FITS processing and S3 artifacts. Jobs (8004) alone issues job UUIDs, handles idempotency, Celery state and result references. Existing Image Processing `eon.image_processing.create_cutout` payload and response remain valid. New Spectral task names are separately allowlisted and routed to the `spectral` queue. `succeeded` remains an accepted legacy Jobs state; Spectral reports it to clients as `completed`.

## HTTP JSON

All coordinates are finite ICRS degrees, `0 <= ra_deg < 360`, `-90 <= dec_deg <= 90`. `fov_deg` is a square display width, limited to 0.01–1 degree and clipped to the one containing tile; it is distinct from Observation's circular SIA2 diameter. `channel_index` is zero based, 0–101.

- `GET /api/v1/spectral/channels/`: returns `{product_id, wavelength_unit:"um", channels:[{channel_index, band, band_channel, wavelength_um, available}]}` from the live official `properties`; 50 and 51 have `available:false`. `product_id` is the official `creator_did`.
- `POST /api/v1/spectral/cubes/` with `{ra_deg,dec_deg,fov_deg}`: returns HTTP 202 `{job_id,status:"pending",status_url}`. Spectral submits to Jobs `{task:"eon.spectral.prepare_cube",idempotency_key:<sha256>,payload:{ra_deg,dec_deg,fov_deg}}` with the existing internal token.
- `GET /api/v1/spectral/jobs/{job_id}/`: proxies Jobs and maps its `state` to `status`; returns `{job_id,status,progress,error,result}`. A completed cube result contains `{product_id,tile_id,object_key,metadata_key,ra_deg,dec_deg,fov_deg,image_unit,channels}`. Keys are persistent S3 keys, never browser URLs.
- `GET /api/v1/spectral/cubes/{tile_id}/channels/{channel_index}/preview/?ra_deg=&dec_deg=&fov_deg=`: returns image/png for an available layer. Blank layers return HTTP 404. The image is cropped using the tile's celestial WCS; its display is for exploration.
- `POST /api/v1/spectral/cubes/{tile_id}/point/` with `{ra_deg,dec_deg,fov_deg,u,v}`: maps normalized preview coordinates (`0 <= u,v <= 1`) to actual ICRS coordinates using the FITS celestial WCS. This prevents interpreting a HiPS HPX tile as a tangent projection in the browser.
- `POST /api/v1/spectral/spectra/` with `{ra_deg,dec_deg,fov_deg}`: returns the same 202 job envelope. The completed result contains `{product_id,tile_id,object_key,unit,wavelength_unit,method,flags_header_names,points}`. Each point has `{channel_index,wavelength_um,flux,nhits,flags,available}`; absent measurements have `flux:null`.

Verified submission request and pending response (the UUID belongs to this test run):

```http
POST /api/v1/spectral/cubes/ HTTP/1.1
Content-Type: application/json

{"ra_deg":60.8109955,"dec_deg":51.3404038,"fov_deg":0.5}
```

```json
{"job_id":"a283c088-17e3-4a15-ba15-102116834608","status":"pending","status_url":"/api/v1/spectral/jobs/a283c088-17e3-4a15-ba15-102116834608/"}
```

The polled job then returned HTTP 200 with `status:"running"`, `progress:null`, `error:null`, `result:null` while IRSA streamed the FITS. It completed with `progress:100`. The `result` field is a typed union by submitted task: the cube result above or the spectrum result above.

Verified completed cube excerpt:

```json
{"job_id":"a283c088-17e3-4a15-ba15-102116834608","status":"completed","progress":100,"error":null,"result":{"product_id":"ivo://IPAC/P/SPHEREx/DR1/cube","tile_id":445,"image_unit":"MJy/sr","object_key":"spectral/dr1/hips_20261026/Norder5/Npix445_cube.fits","metadata_key":"spectral/dr1/metadata/0552b06e35a3a911211ee9d4776611e5c1776dc476a9b6f639acfd1da0051bc2.json"}}
```

This excerpt omits other real fields and 102 `channels` for readability. The actual HTTP response includes all fields.

The spectrum is a quick-look single-pixel surface-brightness sample in the FITS `BUNIT` (normally `MJy/sr`), without invented uncertainty or physical interpretation of FLAGS. `WCS-WAVE` is used through `WCS(IMAGE.header, fobj=hdul)` for the requested position; the global channel table or HiPS properties is only display metadata. `NHITS` is contributing pixel count. `FLAGS` is a raw bitmask; `MSKN*` FITS header labels are returned unchanged in cube metadata. FITS extension names, all `SPECTRAL_CHANNELS` column units and values, and source URL are retained in metadata. The HiPS tutorial warns that cube spectra are for visualization, not science photometry.

The browser shows one `IMAGE` plane at a time in grayscale; the colorful stack is only a channel selector. The graph is empty until a sky point is selected on the image or the user chooses “Trazar espectro del centro”. Its horizontal axis is the position-dependent wavelength in µm, and its vertical axis is the FITS surface-brightness unit. One selected point yields up to 102 samples, with gaps for channels 50/51 or pixels without valid data. Up to three selected points can be compared. This is a single-pixel quick look; the IRSA notebook demonstrates aperture plus background-annulus photometry and explicitly directs scientific users to the SPHEREx Spectrophotometry tool.

Browser clients resolve previews through Spectral's HTTP endpoint. S3 keys remain internal. Poll the job URL until `completed` or `failed` (or `succeeded` from a direct legacy Jobs request). Never infer an observed flux for a blank or invalid pixel.

The service permits browser origins listed in `EON_SPECTRAL_BROWSER_ORIGINS` (default `http://localhost:5173,http://127.0.0.1:5173`). The frontend uses `VITE_SPECTRAL_API_URL` (default `http://localhost:8003`). The `spectral` Celery queue requires a Spectral worker, for example `SERVICE_NAME=spectral celery -A eon_common.celery worker -Q spectral --concurrency=1`; the current `compose.yaml` has no such worker service and was deliberately not changed.

Spectral submission uses a Redis-backed DRF throttle of 30 requests/minute in both Spectral and Jobs; the Image Processing job limit remains 10/min. Jobs HTTP 429 is propagated as 429 with `Retry-After`, rather than mislabeled as 502. Preview requests have a 120/min limit and successful PNGs have a one-day browser cache. The frontend waits and retries at most twice after submission 429, and shows Jobs `progress` while the cube is prepared.

A cold order-5 tile is about 321 MB. IRSA confirmed HTTP 206, byte-range support, an ETag and a 320,909,760-byte size for the documented DR1 tile. Spectral now requests at most four bounded ranges concurrently, validates each `Content-Range` and ETag, and assembles the exact FITS bytes before uploading them to S3. If IRSA ignores Range, it uses the existing resumable serial download. `EON_SPECTRAL_DOWNLOAD_WORKERS` limits the parallel connections to 1–4. A per-process, immutable-tile disk cache (`EON_SPECTRAL_CACHE_DIR`, default `/tmp/eon-spectral-cubes`) reuses the FITS for later spectra and previews on the same service; `EON_SPECTRAL_CACHE_MAX_BYTES` defaults to 700 MiB with oldest-tile eviction. S3 remains the persistent source of truth. Cache locks serialize misses for the same tile, while different tiles can download independently. The progress percentage reflects transfer and processing, not a promised completion time.

After activating these changes, `POST /api/v1/spectral/spectra/` for RA 200°, Dec 42° returned HTTP 202 and its job completed with a 102-point `MJy/sr` spectrum and persistent `object_key`; the worker reported 0.278 s for the cached-tile task. Preview channel 0 returned HTTP 200 in 0.95 s on first access in the API container; channel 1 returned HTTP 200 in 0.092 s after the local tile cache warmed. A cold-tile parallel ingestion for RA 205°, Dec 42° completed with progress 100 and persistent key `spectral/dr1/hips_20261026/Norder5/Npix2666_cube.fits`. Its worker runtime was 381.35 s, compared with 419.14 s for a prior serial ingestion of a different tile. These are different sky tiles and network conditions, so they show modest observed latency improvement, not a controlled speedup ratio. The first parallel attempt of tile 2666 failed due Celery's thread-local task context; the progress callback was moved to the task thread before the successful retry.

For interactive traffic, run at least two Spectral workers or two worker processes against the `spectral` queue. Spectral settings use a prefetch multiplier of one and its tasks acknowledge after completion, so a long tile download does not reserve later spectrum requests from another worker. In the local review setup, two one-process workers were started without changing `compose.yaml`. Cached spectra completed in approximately 0.03–0.3 s of worker time; queue wait and HTTP overhead are separate. Avoid increasing cold-download concurrency without checking IRSA rate limits and available disk space.

## Verification on 2026-11-15 UTC

- `docker compose run ... spectral python manage.py check`: passed.
- `python -m unittest tests.test_spectral_contract -v` inside the Spectral image: 4 passed (a small synthetic FITS regression fixture and an interrupted HTTP stream fixture, not live results).
- Live IRSA `properties` via Spectral: product `ivo://IPAC/P/SPHEREx/DR1/cube`, 102 channels, blank indices 50/51, example HEALPix tile 445.
- Spectral HTTP: channels 200, invalid RA 400, blank preview 404, browser CORS preflight 204, cube submission 202, status 200/pending and 200/running, duplicate submission reused the same UUID. A first temporary worker exposed 200/failed while storage was unavailable. Jobs payload validation accepted both the existing Image Processing cutout and new Spectral payloads. With MinIO running, a fresh job completed 200/completed and progress 100.
- The official `Npix445_cube.fits` was downloaded from the tutorial URL (320,909,760 bytes) and opened with Astropy. `IMAGE` is `(102,512,512)` and `BUNIT=MJy/sr`; `SPECTRAL_CHANNELS` has 102 rows and columns `DETECTOR`, `SUBCHAN`, `WAVELENGTH` (um), `WL_MIN` (um), `WL_MAX` (um), `R`, `R_STD`, `BANDWIDTH` (um). The 102 FITS wavelength values differed from `properties` by at most 2.37e-7 µm (float32 rounding) and had zero availability mismatches. `FLAGS` has 22 raw `MSKN*` labels. At RA 60.8109955°, Dec 51.3404038°, index 0 returned wavelength 0.753000020980835 µm, flux 0.3881065845489502 MJy/sr, NHITS 7, raw FLAGS 2097152. Indices 50 and 51 returned null flux and zero hits. Index 101 returned wavelength 4.98799991607666 µm, flux 3.3104512691497803 MJy/sr, NHITS 13. A 36,747-byte PNG preview had the correct PNG signature; its center mapped through celestial WCS to RA 60.81978319783198°, Dec 51.340543352874356°.
- The frontend `pnpm build` passed and Vite served `/spectral` with HTTP 200.

- The successful worker downloaded the tile directly from IRSA, then stored `spectral/dr1/hips_20261026/Norder5/Npix445_cube.fits` in MinIO. A separate S3 `HEAD` returned 320,909,760 bytes and `application/fits`. `GET /api/v1/spectral/cubes/445/channels/0/preview/` returned HTTP 200, `image/png`, 36,747 bytes. `POST /api/v1/spectral/cubes/445/point/` returned HTTP 200 with RA 60.81978319783198°, Dec 51.340543352874356° for `(u,v)=(0.5,0.5)`.
- A second cube job with `fov_deg=0.4` ran against the persisted tile using the final worker code. It completed with progress 100 and returned 102 channel rows, 102 `SPECTRAL_CHANNELS` FITS table rows, 22 raw FLAGS header labels, the persistent FITS key and metadata key `spectral/dr1/metadata/2a6c86da1720414e186bed434499ae9a33ae5686cd761664ee24240a6f3270ef.json`.
- `POST /api/v1/spectral/spectra/` at that point returned HTTP 202; Jobs `41a43133-5ad3-4232-a5b5-a7d2af3f8690` completed with 102 points, unit `MJy/sr`, 22 unmodified `MSKN*` labels, null flux and zero NHITS for indices 50 and 51, and `object_key=spectral/dr1/spectra/95593c1bb51c106bf08cdf2ad5b42a67cbb34ccb24b15ffd913f0cdba750ddaa.json`. Reading that key back from MinIO returned the 102-point JSON.
- Headless Chrome opened `/spectral?ra=60.8109955&dec=51.3404038&fov=0.5&ch=1`, displayed the actual preview, clicked its center, received HTTP 200 for WCS point conversion, HTTP 202 for spectrum submission and HTTP 200 for polling, then rendered a spectrum chart and `NHITS 8 · FLAGS 2097152`. No browser JavaScript errors were captured.

The official tile 445 passed a live IRSA → Jobs worker → MinIO ingestion run and a subsequent browser → Spectral → Jobs → browser run against that persisted product. A separate browser cache-miss run for an unprepared tile reached `running` with download progress but failed on an IRSA read timeout. The downloader was then updated to resume from its last byte using a validated HTTP `Range`; a regression test passed, but the revised downloader was not rerun against that live tile. `compose.yaml` still references `minio/minio` and `minio/mc` images that the registry rejected (`pull access denied`); `quay.io/minio/minio` returned HTTP 401 in this environment. For the successful storage test, the official GitHub release binary `RELEASE.2025-09-07T16-13-09Z` was downloaded, SHA-256 checked against its release asset, and started as a temporary MinIO container on the Compose network; the bucket was created with boto3. The frontend, Spectral API, Jobs API, worker and temporary MinIO were left running for review. A normal fresh `docker compose up` still needs reachable MinIO images or an equivalent manual MinIO setup; no Compose edits were made.
