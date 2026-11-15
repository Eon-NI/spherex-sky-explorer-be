# IRSA integration and scientific constraints

## MVP del hackathon

V?ase [BE01_MVP.md](BE01_MVP.md) para contratos, errores, pruebas y l?mites actuales.
TAP usa una Session Requests propia con timeout efectivo de conexi?n/lectura y no
modifica el singleton global Astroquery. El product_id sigue asociado a la consulta.
El JSON exitoso se conserva; par?metros no soportados se rechazan con 400.
Redis indisponible en rate limiting devuelve 503 antes de IRSA; no habilita acceso ilimitado.

## Sources

The [SPHEREx tutorial index](https://caltech-ipac.github.io/irsa-tutorials/spherex/), [SIA2/FITS tutorial](https://caltech-ipac.github.io/irsa-tutorials/spherex-intro/), [TAP/cutout tutorial](https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts/), and [IRSA API guide](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-access/) define the queries and products used here.

## SIA2 versus TAP

SIA2 uses a direct GET to IRSA's documented `https://irsa.ipac.caltech.edu/SIA` endpoint with `COLLECTION`, `POS=CIRCLE ra dec radius_deg`, and `RESPONSEFORMAT=VOTABLE`. This follows the locally validated `SPHERExProofOfConcept/spherex_poc/archive.py` request and avoids PyVO's separate `/SIA/capabilities` discovery, which returned HTTP 502 in the local Docker test. The default collections are `spherex_qr3` and `spherex_dr1` in that priority order; each is queried separately, as in the PoC. Callers may also select `spherex_dr1_deep` and `spherex_qr3_deep`. SIA2 results contain `obs_publisher_did`, `obs_id`, `obs_collection`, `access_url`, `access_format`, sky location/footprint, bandpass, temporal and wavelength bounds. Absent optional values are `null` and listed in `missing_fields`. The adapter rejects missing/error/overflow `QUERY_STATUS`, checks the MJD day units of `t_min`/`t_max` when present, and caps response size. `em_min`/`em_max` are in metres. The SIA2 ingestion may lag roughly a day behind new releases.

TAP queries `spherex.artifact` joined to `spherex.plane`, with `CONTAINS(POINT('ICRS', ra, dec), p.poly)` and `p.energy_bandpassname = 'SPHEREx-Dn'`. It returns individual product cutout URLs and `p.time_bounds_lower` in chronological order. The implementation selects `a.uri` additionally as the product identifier, adds validated `TOP N`/`maxrec`, and preserves the numeric `time_bounds_lower` value without inventing an epoch or converting it to a calendar date. No row is equated with a spectral cube channel. Only numeric coordinates, finite cutout size, a detector from D1–D6, and a bounded integer limit enter ADQL. The default API maximum is 100 rows, configurable with `EON_TAP_MAX_RESULTS` up to the domain ceiling of 500.

Equivalent documented query for M101 (the adapter adds `TOP`, `a.uri AS artifact_uri` and `maxrec`):

```sql
SELECT 'https://irsa.ipac.caltech.edu/' || a.uri ||
       '?center=210.80227,54.34895d&size=0.1' AS uri,
       p.time_bounds_lower
FROM spherex.artifact a
JOIN spherex.plane p ON a.planeid = p.planeid
WHERE 1 = CONTAINS(POINT('ICRS', 210.80227, 54.34895), p.poly)
  AND p.energy_bandpassname = 'SPHEREx-D2'
ORDER BY p.time_bounds_lower
```

## Cutouts and provenance

The cutout URL comes from TAP, not a client URL: `https://irsa.ipac.caltech.edu/{a.uri}?center={ra},{dec}d&size={size_deg}`. Observation stores the artifact URI, URL, query position/size/detector and original TAP observation time value. Image Processing retrieves this record over Observation's API and accepts only the exact TAP-verified position/size. It checks HTTPS, the exact IRSA host, `/ibe/data/spherex/{dr1,qr3}/level2/`, `.fits`, and matching query parameters. Redirects are disabled. The downloader streams into a temporary file with a configurable byte cap, then Astropy validates the MEF before S3 upload. A SHA-256 key of product and cutout parameters is used for idempotency and S3 cache lookup. `CutoutRecord` preserves source URL, artifact path, processing version, detector and S3 key.

## FITS and WCS

Documented Spectral Image MEFs contain `PRIMARY`, `IMAGE`, `FLAGS`, `VARIANCE`, `ZODI`, `EPSF`, and `WCS-WAVE`; extensions may be absent in other products, so the reader requires IMAGE and treats FLAGS/WCS-WAVE as optional. It exposes dimensions, BUNIT, DETECTOR, OBSID, DATE, PSF_FWHM, finite-pixel count and basic statistics. FLAGS dimensions and raw `MSKN*` labels are exposed without modifying the bitmap or claiming a bit interpretation. Primary `VERSION` is processing provenance.

Spatial coordinates use `WCS(image.header).celestial` and Astropy sky/pixel transformations, preserving any spatial distortion in the header. If `WCS-WAVE` is present, `WCS(header=image.header, fobj=hdul, key='W')` is used with `spectral.sip = None` as directed by IRSA; the reported wavelength and bandwidth in µm correspond to the requested pixel. Spectral values vary across the detector. The FITS `DATE` header may describe file processing; chronological order uses TAP `time_bounds_lower` instead.

## Known limits

- The public search API accepts a circle; there is no rectangular FOV search endpoint yet. TAP verifies that its point is inside each product, and the cutout service uses `size_deg` for the returned square.
- The repository persists the modeled optional ObsCore fields; it is not a full ObsCore mirror. Products are deduplicated by publisher DID before search persistence/cache and sync.
- TAP depends on IRSA availability and may fail or time out. A 502 indicates upstream failure; an empty successful result has `count: 0`.
- The download cap defaults to 128 MiB, which may reject large cutouts. FITS statistics scan the selected IMAGE once; matrices are never returned in JSON. A separate rate-limited endpoint streams `application/fits` from S3 for viewers.
- Celery result and idempotency keys expire after seven days. S3 objects and cutout provenance remain until explicitly removed.

## On-demand cache and daily synchronization

The SIA2 endpoint accepts either `radius_deg` or `fov_deg`, never both. `fov_deg` is the diameter of the **circular** SIA2 region and is divided by two before the direct HTTP SIA request. A rectangular sky window would require a separate, explicitly defined geometry and is not implied by the current API. TAP uses point-in-polygon containment and `size_deg` for the returned cutout square; this is not a rectangular catalog search.

For an exact SIA2 or TAP query, Redis is the hot key store and PostgreSQL holds an ID-only query snapshot. Observation rows hold source values, optional-field markers and processing version parsed from the documented `level2/[planning period]/[processing version]/[detector]/...fits` path. The snapshot expires after `EON_OBSERVATION_CACHE_TTL_SECONDS` (default 21,600 seconds). Query results are rehydrated from Observation's own tables. Redis query-cache failure falls back to PostgreSQL (rate-limiter failure independently returns 503); an expired/missing snapshot causes a new IRSA request.

Celery Beat schedules `sync_irsa` every 86,400 seconds. It uses a Redis lock to avoid concurrent runs, and queries all four current spectral-image collections in each configured region. A bulk comparison on `obs_publisher_did` writes only new or changed metadata. When changes occur, a PostgreSQL catalog-generation counter advances and query snapshots are cleared; Redis keys from the old generation expire naturally. This invalidates both SIA2 and TAP cache entries without scanning Redis. The sync does not download cutouts. Region selection is explicit because scanning the full sky would require a separate archive-friendly partition plan.

Security limits: query coordinates and detector values are checked before ADQL interpolation; TAP uses `TOP` and `maxrec`; DRF throttles expensive endpoints through Redis. Cutouts accept only a stored product ID and exact TAP-verified coordinates, enforce HTTPS/IRSA host and level2 path, reject traversal and redirects, stream with a byte cap, and retry transient transport failures with backoff. Secrets belong in `.env`, never in the repository. The S3 bucket is created by Compose's `minio_setup` service. A cutout SHA-256 idempotency key prevents duplicate queued work during its seven-day Redis lifetime.
