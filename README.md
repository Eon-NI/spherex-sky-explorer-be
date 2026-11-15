# EON — SPHEREx Sky Explorer Backend

Backend para descubrir observaciones SPHEREx mediante SIA2 y TAP, recuperar cutouts FITS verificados de IRSA y examinar IMAGE, FLAGS y WCS con Astropy. Spectral conserva contratos para trabajo futuro.

## Arquitectura

Cada proceso Django selecciona un único contexto con `SERVICE_NAME` y utiliza una base PostgreSQL propia. La carpeta de cada servicio separa `domain` (tipos y reglas), `application` (casos de uso y puertos `Protocol`), `infrastructure` (IRSA, PostgreSQL o S3) y `presentation` (API). `eon_common` contiene únicamente configuración y transporte compartidos.

| Servicio | Puerto | Estado |
| --- | ---: | --- |
| Observation | 8001 | SIA2/TAP con caché Redis/PostgreSQL y sincronización regional diaria |
| Image Processing | 8002 | Cutouts verificados, almacenamiento S3 y metadatos FITS/WCS |
| Spectral | 8003 | Cubo HiPS DR1, previews y espectros exploratorios |
| Jobs | 8004 | Cola Celery, idempotencia Redis y consulta de estados/resultados |

La consulta por FOV rectangular, timelapse, difference y stack siguen pendientes. La exploración del cubo HiPS DR1 y la extracción espectral exploratoria se documentan en [docs/SPECTRAL_EXPLORATION.md](docs/SPECTRAL_EXPLORATION.md). `radius_deg` es el radio de una búsqueda circular en Observation; su `fov_deg` es el diámetro, no el lado de un rectángulo. QR3 tiene prioridad en la búsqueda SIA2 por defecto y DR1 complementa. TAP conserva el orden cronológico. La sincronización diaria revisa las cuatro colecciones en regiones configuradas y actualiza solo metadatos nuevos o modificados.

Spectral requiere un worker Celery escuchando la cola `spectral`; el `compose.yaml` actual no lo declara. La verificación de esta iteración completó el flujo con FITS real de IRSA y MinIO temporal, iniciado desde un binario oficial porque el registro rechazó las imágenes MinIO referidas por Compose. Para un arranque limpio se necesita una imagen MinIO accesible o iniciar MinIO por separado. Consulte las pruebas y los payloads en la documentación espectral.

## Requisitos

Docker Engine con Compose, o Python 3.12/3.13, PostgreSQL 17 y Redis 7. Copiar `.env.example` a `.env` y cambiar todas las claves. Definir `IRSA_SYNC_REGIONS` como lista JSON de regiones (`ra_deg`, `dec_deg`, `radius_deg`) o las tres variables individuales `IRSA_SYNC_RA_DEG`, `IRSA_SYNC_DEC_DEG` y `IRSA_SYNC_RADIUS_DEG` para activar el sync cada 24 horas. Sin regiones, Beat omite la tarea. El radio máximo de SIA2 es 1 grado y el FOV circular máximo es 2 grados de diámetro, ajustable hacia abajo con `EON_MAX_FOV_DEG`.

## MVP actual: Django en Windows

Consultar [alcance, contratos y evidencia EON-BE-01](docs/BE01_MVP.md).
Observation s?lo descubre/resuelve referencias; BE-02 obtiene/procesa FITS y BE-03
coordina jobs. La suite de BE-01 no requiere MinIO.

Para el hackathon, MinIO ser? un contenedor independiente con imagen
`alpine/minio:RELEASE.2025-10-15T17-29-55Z`; su disponibilidad no se ha verificado.
Django nativo Windows usar? `S3_ENDPOINT_URL=http://127.0.0.1:9000` y las variables
`S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` para BE-02. No se modific? Compose.
`manage.py` no carga `.env` autom?ticamente: el entorno debe exportar sus variables.
Para ejecutar Observation localmente, configurar PostgreSQL/Redis accesibles desde
Windows y `SERVICE_NAME=observation`, y ejecutar `python manage.py runserver 8001`.
Los clientes locales de BE-02 deber?n usar `OBSERVATION_SERVICE_URL=http://127.0.0.1:8001`.

Validaci?n MVP: **79 pruebas pasaron, 5 integraciones omitidas; 5 smoke pasaron**.
SIA2 real pas?; TAP real limit=5 agot? los timeouts de 30 y 60 segundos de forma
controlada. **No se declara cerrado el MVP hasta validar TAP satisfactoriamente.**

## Configuraci?n Docker hist?rica

La siguiente configuraci?n describe el Compose existente, no el arranque de MinIO
independiente acordado para el hackathon. `.env.example` prioriza ahora el endpoint
S3 de Windows; esa direcci?n debe adaptarse si Django se ejecuta en contenedor.


```bash
cp .env.example .env
# Editar contraseñas y clave Django
# Inicializar las cuatro bases en el primer arranque
docker compose up --build -d
for service in observation image_processing spectral jobs; do
  docker compose exec "$service" python manage.py migrate --noinput
done
```

Búsqueda SIA2 (GET directo a `/SIA`, verificado en el PoC local; sin descubrimiento de `/SIA/capabilities`):

```bash
curl 'http://localhost:8001/api/v1/observations/search/?ra_deg=304.693508808&dec_deg=42.4436872991&radius_deg=0.0002777778'
```

El ejemplo de coordenadas procede del [tutorial oficial de IRSA](https://caltech-ipac.github.io/irsa-tutorials/spherex-intro/), que documenta `query_sia`, las colecciones `spherex_dr1` y `spherex_qr3`, y los campos `obs_publisher_did`, `obs_collection`, `access_url`, `s_ra` y `s_dec`. Los datos disponibles pueden variar con el tiempo. La respuesta 502 indica error de consulta externa, no ausencia de observaciones.

La búsqueda exacta consulta primero Redis/PostgreSQL durante `EON_OBSERVATION_CACHE_TTL_SECONDS` (6 horas por defecto). La búsqueda temporal usa `/api/v1/observations/temporal/`; su `product_id` se envía a `POST /api/v1/cutouts/` en el puerto 8002. La respuesta 202 contiene un `status_url` para consultar el trabajo; al terminar, `/api/v1/cutouts/{cutout_id}/metadata/` devuelve los metadatos y `/api/v1/cutouts/{cutout_id}/fits/` transmite el FITS binario desde S3 para visualización. Véanse [flujo completo](docs/BACKEND_WORKFLOW.md) e [integración IRSA](docs/IRSA_INTEGRATION.md).

Cada proceso expone `/health/`, `/api/v1/schema/` (OpenAPI) y `/api/v1/docs/` (Swagger). MinIO está en `localhost:9000` y su consola en `localhost:9001`; Compose crea el bucket `S3_BUCKET` mediante `minio_setup`.

El health check de Django está en **`/health/`**, no en `/api/v1/health/`. Un 404 de `/favicon.ico` sólo indica que no hay icono configurado. La URL `/SIA` del PoC pertenece al servicio externo de IRSA; en EON se consulta desde el adaptador de Observation mediante `GET /api/v1/observations/search/`.

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

Verificación local posterior (14 de noviembre de 2026): `docker compose exec observation python manage.py migrate --noinput` terminó sin migraciones pendientes; `/health/`, `/api/v1/docs/` y una búsqueda SIA2 en `/api/v1/observations/search/` respondieron HTTP 200 en el puerto 8001. La búsqueda M101 (`ra_deg=210.80227`, `dec_deg=54.34895`, `radius_deg=0.0002777778`, `collections=spherex_dr1`) devolvió 246 registros reales en ese momento. `pytest -q -p no:cacheprovider tests/test_observation.py tests/test_sync.py tests/test_search_cache.py tests/test_tap_cutout.py` pasó con 22 pruebas en el contenedor. El conteo publicado puede cambiar.

Verificación en el entorno de inicialización (14 de noviembre de 2026): `python3 -m compileall -q eon_common services tests` pasó; una prueba directa de dominio y casos de uso pasó; `docker compose config --quiet` pasó con `.env.example` copiado temporalmente a `.env`. `python3 -m pytest -q` no pudo ejecutarse porque `pytest` no está instalado y el índice de paquetes no resolvió DNS. Una consulta directa al endpoint SIA2 documentado de IRSA respondió `QUERY_STATUS=OK`, 51 campos y 414 filas para `COLLECTION=spherex_dr1&POS=circle+127.69444+-39.17760+0.01`; los cinco campos usados por el adaptador estaban presentes. Esta comprobación valida el servicio remoto y su esquema, pero no equivale a una prueba del proceso Django.

## Iteración científica actual

`EON_TAP_MAX_RESULTS` limita el volumen TAP; `EON_MAX_CUTOUT_BYTES` limita cada descarga; `IRSA_TIMEOUT_SECONDS` fija la lectura HTTP de SIA2/TAP (1?120 s; conexi?n 15 s); `EON_INTERNAL_TOKEN` protege la creación de jobs entre servicios. Los endpoints costosos tienen límites de tasa respaldados por Redis; los workers usan concurrencia 1 (sync) y 2 (cutouts). Ver [AGENTS.md](AGENTS.md) para convenciones y estado. La verificación de esta iteración se reporta al finalizar; no debe confundirse la consulta HTTP directa a IRSA con una ejecución de la suite de integración Django/Astropy.

### Flujo bajo demanda y sincronización

1. `GET /api/v1/observations/search/?ra_deg=...&dec_deg=...&fov_deg=...` devuelve resultados SIA2 de una región **circular**. Redis conserva IDs de consultas recientes; PostgreSQL mantiene los metadatos y una instantánea por consulta. Un acierto válido no consulta IRSA. `GET /api/v1/observations/temporal/` usa el mismo patrón de caché para TAP.
2. Un `product_id` de TAP se envía a Image Processing. Job Service asigna un UUID idempotente, Celery descarga el cutout bajo demanda, Astropy inspecciona el FITS y S3 almacena los bytes. La respuesta del job y el endpoint de metadatos contienen referencias y valores científicos compactos.
3. Celery Beat ejecuta `sync_irsa` cada 24 horas. Por cada región configurada consulta `spherex_qr3`, `spherex_qr3_deep`, `spherex_dr1` y `spherex_dr1_deep`; PostgreSQL modifica solo filas nuevas o cuyo contenido cambió. Una generación duradera en PostgreSQL invalida las cachés SIA2 y TAP cuando hay cambios. Los FITS no se descargan durante el sync.

La caché es por consulta exacta y caduca; no representa un índice espacial completo. El sync solo cubre las regiones configuradas. Para operar una cobertura de todo el cielo se necesitaría un plan de particionado y cuotas específico del archivo.
