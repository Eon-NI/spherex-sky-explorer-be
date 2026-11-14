# EON — SPHEREx Sky Explorer Backend

Backend para descubrir observaciones SPHEREx mediante SIA2 y TAP, recuperar cutouts FITS verificados de IRSA y examinar IMAGE, FLAGS y WCS con Astropy. Spectral conserva contratos para trabajo futuro.

## Arquitectura

Cada proceso Django selecciona un único contexto con `SERVICE_NAME` y utiliza una base PostgreSQL propia. La carpeta de cada servicio separa `domain` (tipos y reglas), `application` (casos de uso y puertos `Protocol`), `infrastructure` (IRSA, PostgreSQL o S3) y `presentation` (API). `eon_common` contiene únicamente configuración y transporte compartidos.

| Servicio | Puerto | Estado |
| --- | ---: | --- |
| Observation | 8001 | SIA2/TAP con caché Redis/PostgreSQL y sincronización regional diaria |
| Image Processing | 8002 | Cutouts verificados, almacenamiento S3 y metadatos FITS/WCS |
| Spectral | 8003 | Contrato de cubo y extracción espectral |
| Jobs | 8004 | Cola Celery, idempotencia Redis y consulta de estados/resultados |

La consulta por FOV rectangular, HiPS, timelapse, difference, stack y extracción espectral siguen pendientes. `radius_deg` es el radio de una búsqueda circular; `fov_deg` es su diámetro, no el lado de un rectángulo. QR3 tiene prioridad en la búsqueda SIA2 por defecto y DR1 complementa. TAP conserva el orden cronológico. La sincronización diaria revisa las cuatro colecciones en regiones configuradas y actualiza solo metadatos nuevos o modificados.

## Requisitos

Docker Engine con Compose, o Python 3.12/3.13, PostgreSQL 17 y Redis 7. Copiar `.env.example` a `.env` y cambiar todas las claves. Definir `IRSA_SYNC_REGIONS` como lista JSON de regiones (`ra_deg`, `dec_deg`, `radius_deg`) o las tres variables individuales `IRSA_SYNC_RA_DEG`, `IRSA_SYNC_DEC_DEG` y `IRSA_SYNC_RADIUS_DEG` para activar el sync cada 24 horas. Sin regiones, Beat omite la tarea. El radio máximo de SIA2 es 1 grado y el FOV circular máximo es 2 grados de diámetro, ajustable hacia abajo con `EON_MAX_FOV_DEG`.

## Inicio con Docker

```bash
cp .env.example .env
# Editar contraseñas y clave Django
# Inicializar las cuatro bases en el primer arranque
docker compose up --build -d
for service in observation image_processing spectral jobs; do
  docker compose exec "$service" python manage.py migrate --noinput
done
```

Búsqueda SIA2:

```bash
curl 'http://localhost:8001/api/v1/observations/search/?ra_deg=304.693508808&dec_deg=42.4436872991&radius_deg=0.0002777778'
```

El ejemplo de coordenadas procede del [tutorial oficial de IRSA](https://caltech-ipac.github.io/irsa-tutorials/spherex-intro/), que documenta `query_sia`, las colecciones `spherex_dr1` y `spherex_qr3`, y los campos `obs_publisher_did`, `obs_collection`, `access_url`, `s_ra` y `s_dec`. Los datos disponibles pueden variar con el tiempo. La respuesta 502 indica error de consulta externa, no ausencia de observaciones.

La búsqueda exacta consulta primero Redis/PostgreSQL durante `EON_OBSERVATION_CACHE_TTL_SECONDS` (6 horas por defecto). La búsqueda temporal usa `/api/v1/observations/temporal/`; su `product_id` se envía a `POST /api/v1/cutouts/` en el puerto 8002. La respuesta 202 contiene un `status_url` para consultar el trabajo; al terminar, `/api/v1/cutouts/{cutout_id}/metadata/` devuelve los metadatos y `/api/v1/cutouts/{cutout_id}/fits/` transmite el FITS binario desde S3 para visualización. Véanse [flujo completo](docs/BACKEND_WORKFLOW.md) e [integración IRSA](docs/IRSA_INTEGRATION.md).

Cada proceso expone `/health/`, `/api/v1/schema/` (OpenAPI) y `/api/v1/docs/` (Swagger). MinIO está en `localhost:9000` y su consola en `localhost:9001`; Compose crea el bucket `S3_BUCKET` mediante `minio_setup`.

## Desarrollo y pruebas

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
ruff check .
SERVICE_NAME=observation python manage.py check
```

Las pruebas unitarias sustituyen las llamadas externas. Las pruebas de integración real se activan con `EON_RUN_LIVE_IRSA=1 pytest -q tests/integration -m integration`; descargan un cutout a un directorio temporal. La búsqueda sola no descarga FITS.

## Estado de verificación

Verificación en el entorno de inicialización (14 de noviembre de 2026): `python3 -m compileall -q eon_common services tests` pasó; una prueba directa de dominio y casos de uso pasó; `docker compose config --quiet` pasó con `.env.example` copiado temporalmente a `.env`. `python3 -m pytest -q` no pudo ejecutarse porque `pytest` no está instalado y el índice de paquetes no resolvió DNS. Una consulta directa al endpoint SIA2 documentado de IRSA respondió `QUERY_STATUS=OK`, 51 campos y 414 filas para `COLLECTION=spherex_dr1&POS=circle+127.69444+-39.17760+0.01`; los cinco campos usados por el adaptador estaban presentes. Esta comprobación valida el servicio remoto y su esquema, pero no equivale a una prueba del proceso Django.

## Iteración científica actual

`EON_TAP_MAX_RESULTS` limita el volumen TAP; `EON_MAX_CUTOUT_BYTES` limita cada descarga; `IRSA_TIMEOUT_SECONDS` configura el timeout de Astroquery; `EON_INTERNAL_TOKEN` protege la creación de jobs entre servicios. Los endpoints costosos tienen límites de tasa respaldados por Redis; los workers usan concurrencia 1 (sync) y 2 (cutouts). Ver [AGENTS.md](AGENTS.md) para convenciones y estado. La verificación de esta iteración se reporta al finalizar; no debe confundirse la consulta HTTP directa a IRSA con una ejecución de la suite de integración Django/Astropy.

### Flujo bajo demanda y sincronización

1. `GET /api/v1/observations/search/?ra_deg=...&dec_deg=...&fov_deg=...` devuelve resultados SIA2 de una región **circular**. Redis conserva IDs de consultas recientes; PostgreSQL mantiene los metadatos y una instantánea por consulta. Un acierto válido no consulta IRSA. `GET /api/v1/observations/temporal/` usa el mismo patrón de caché para TAP.
2. Un `product_id` de TAP se envía a Image Processing. Job Service asigna un UUID idempotente, Celery descarga el cutout bajo demanda, Astropy inspecciona el FITS y S3 almacena los bytes. La respuesta del job y el endpoint de metadatos contienen referencias y valores científicos compactos.
3. Celery Beat ejecuta `sync_irsa` cada 24 horas. Por cada región configurada consulta `spherex_qr3`, `spherex_qr3_deep`, `spherex_dr1` y `spherex_dr1_deep`; PostgreSQL modifica solo filas nuevas o cuyo contenido cambió. Una generación duradera en PostgreSQL invalida las cachés SIA2 y TAP cuando hay cambios. Los FITS no se descargan durante el sync.

La caché es por consulta exacta y caduca; no representa un índice espacial completo. El sync solo cubre las regiones configuradas. Para operar una cobertura de todo el cielo se necesitaría un plan de particionado y cuotas específico del archivo.
