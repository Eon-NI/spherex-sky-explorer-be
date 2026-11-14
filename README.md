# EON — SPHEREx Sky Explorer Backend

Backend inicial para descubrir observaciones SPHEREx en IRSA. La primera función operativa consulta el servicio SIA mediante `astroquery.ipac.irsa.Irsa.query_sia`; los demás contextos exponen contratos tipados y están preparados para implementaciones futuras.

## Arquitectura

Cada proceso Django selecciona un único contexto con `SERVICE_NAME` y utiliza una base PostgreSQL propia. La carpeta de cada servicio separa `domain` (tipos y reglas), `application` (casos de uso y puertos `Protocol`), `infrastructure` (IRSA, PostgreSQL o S3) y `presentation` (API). `eon_common` contiene únicamente configuración y transporte compartidos.

| Servicio | Puerto | Estado |
| --- | ---: | --- |
| Observation | 8001 | Búsqueda SIA real y sincronización por región configurada |
| Image Processing | 8002 | Contratos FITS/WCS, cutout, blink, difference, stack y timelapse; adaptador S3 |
| Spectral | 8003 | Contrato de cubo y extracción espectral |
| Jobs | 8004 | Contratos de cola, estados y resultados |

La integración TAP, la consulta por FOV rectangular, el procesamiento FITS, HiPS y la extracción espectral aún no se ofrecen como endpoints. Los contratos evitan atribuir resultados científicos no calculados. La sincronización actual consulta periódicamente una región configurada y hace upsert por `obs_publisher_did`; no recorre todo el archivo IRSA.

## Requisitos

Docker Engine con Compose, o Python 3.12/3.13, PostgreSQL 17 y Redis 7. Copiar `.env.example` a `.env` y cambiar todas las claves. Definir `IRSA_SYNC_RA_DEG`, `IRSA_SYNC_DEC_DEG` y `IRSA_SYNC_RADIUS_DEG` para activar la sincronización horaria; sin las tres variables, Beat omite la tarea. El radio máximo aceptado por la API es 1 grado para limitar consultas accidentales muy amplias.

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

La búsqueda disponible:

```bash
curl 'http://localhost:8001/api/v1/observations/search/?ra_deg=304.693508808&dec_deg=42.4436872991&radius_deg=0.0002777778'
```

El ejemplo de coordenadas procede del [tutorial oficial de IRSA](https://caltech-ipac.github.io/irsa-tutorials/spherex-intro/), que documenta `query_sia`, las colecciones `spherex_dr1` y `spherex_qr3`, y los campos `obs_publisher_did`, `obs_collection`, `access_url`, `s_ra` y `s_dec`. Los datos disponibles pueden variar con el tiempo. La respuesta 502 indica error de consulta externa, no ausencia de observaciones.

Cada proceso expone `/health/`, `/api/v1/schema/` (OpenAPI) y `/api/v1/docs/` (Swagger). MinIO está en `localhost:9000` y su consola en `localhost:9001`; el bucket `S3_BUCKET` se debe crear antes de usar almacenamiento científico.

## Desarrollo y pruebas

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
ruff check .
SERVICE_NAME=observation python manage.py check
```

Las pruebas unitarias sustituyen la llamada externa para verificar el contrato y la construcción de la consulta. Para comprobar IRSA de extremo a extremo, ejecutar la llamada `curl` anterior con acceso a Internet. No se descarga ningún FITS durante la búsqueda.

## Estado de verificación

Verificación en el entorno de inicialización (14 de noviembre de 2026): `python3 -m compileall -q eon_common services tests` pasó; una prueba directa de dominio y casos de uso pasó; `docker compose config --quiet` pasó con `.env.example` copiado temporalmente a `.env`. `python3 -m pytest -q` no pudo ejecutarse porque `pytest` no está instalado y el índice de paquetes no resolvió DNS. Una consulta directa al endpoint SIA2 documentado de IRSA respondió `QUERY_STATUS=OK`, 51 campos y 414 filas para `COLLECTION=spherex_dr1&POS=circle+127.69444+-39.17760+0.01`; los cinco campos usados por el adaptador estaban presentes. Esta comprobación valida el servicio remoto y su esquema, pero no equivale a una prueba del proceso Django.
