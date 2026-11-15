# EON-BE-01: Scientific Data Access MVP

## Alcance y estado

Observation descubre y resuelve referencias. No descarga FITS ni realiza cutouts,
frames, timelapse, almacenamiento S3 o coordinaci?n de jobs. Los contratos de
Image Processing y SourceProduct se mantienen, sin migraciones ni dependencias nuevas.

**Implementaci?n y pruebas deterministas completas; aceptaci?n final pendiente de
una respuesta TAP real satisfactoria.** No se declara cerrado EON-BE-01.

## Endpoints y contratos

### B?squeda SIA2

```http
GET /api/v1/observations/search/?collections=spherex_dr1&ra_deg=304.693508808&dec_deg=42.4436872991&radius_deg=0.0002777778
```

Respuesta: `{count, geometry: {type: "circle", radius_deg}, results: [...]}`.
`radius_deg` es radio y `fov_deg` di?metro circular en grados; se requiere exactamente
uno. RA ICRS en [0,360), Dec en [-90,90], ambos finitos. Colecciones: QR3/DR1,
wide/deep; por defecto QR3 y DR1. Repetir `collections=...` para varias colecciones.
La huella `s_region` intersecta la regi?n; el centro de la imagen puede estar fuera.

Se conservan DID, obs_id, URL, huella, bandpass, versi?n, tiempos MJD en d?as (UTC
seg?n SIA2) y l?mites espectrales en metros. Campos opcionales ausentes siguen
siendo null y aparecen en missing_fields. Las filas con el mismo publisher_did se
normalizan antes de persistencia/cach?; se conserva la primera seg?n prioridad del
proveedor. Distintos DID/versiones no se fusionan como si fueran una exposici?n.

Un acierto de cach? no consulta IRSA. Las pruebas HTTP verifican que la respuesta
fresca y la rehidratada tienen el mismo contenido y conteo, incluidos vac?os.

### B?squeda TAP

```http
GET /api/v1/observations/temporal/?ra_deg=210.80227&dec_deg=54.34895&size_deg=0.1&detector=SPHEREx-D2&limit=5
```

Respuesta sin cambios: `{count, results: [...]}`. Cada producto contiene exactamente:

```json
{
  "product_id": "<SHA-256 de 64 caracteres>",
  "artifact_uri": "<ruta original devuelta por TAP>",
  "cutout_url": "<URL IRSA con center y size verificados>",
  "time_bounds_lower": 0.0,
  "ra_deg": 210.80227,
  "dec_deg": 54.34895,
  "size_deg": 0.1,
  "detector": "SPHEREx-D2"
}
```

Este JSON describe el contrato; `time_bounds_lower=0.0` es ilustrativo, no un dato real.
El valor TAP original s?lo ordena: no se le asigna silenciosamente una ?poca/escala
ni se convierte DATE de procesamiento en fecha de observaci?n.

TAP mantiene el JOIN artifact/plane y CONTAINS del tutorial. `size_deg` es el lado
del recorte cuadrado, no una b?squeda rectangular de cat?logo. Detector D1?D6;
limit de 1 al m?ximo del servidor (100 por defecto; techo de dominio 500).
Orden determinista por tiempo y artifact_uri; no devuelve product_id repetidos.

**TOP/limit/maxrec devuelven un prefijo cronol?gico acotado, no paginaci?n ni una
selecci?n distribuida por todo el intervalo.** Si count alcanza limit puede haber
m?s datos. Si hubo duplicados, count menor que limit tampoco prueba que el cat?logo
est? completo. No se proporciona total global ni continuaci?n. OVERFLOW por maxrec es
compatible con este contrato limitado; errores del proveedor devuelven 502.

Cada consulta crea su propio cliente Astroquery y Session Requests. La sesi?n fija
`timeout=(15, IRSA_TIMEOUT_SECONDS)` incluso cuando PyVO omite timeout o pasa None;
lectura limitada a 1?120 s (60 por defecto), sin redirecciones ni reintentos autom?ticos.
Son l?mites de conexi?n/inactividad de lectura, no un plazo total de toda la respuesta.
El deadline del servidor HTTP debe permitir ese presupuesto si se cambia el despliegue.

### Detalle y contrato BE-02

```http
GET /api/v1/observations/temporal/{product_id}/
```

Devuelve exactamente el mismo objeto individual de results. Se resuelve desde
Observation PostgreSQL mediante GetTemporalProduct. El identificador es estable
para artifact + posici?n + tama?o + detector + tiempo, **no una identidad cient?fica
universal de exposici?n**. No se modific? su algoritmo ni SourceProduct.

BE-02 puede usar ObservationApiResolver.get(product_id), comprobar la geometr?a y
la URL y obtener el recurso despu?s. Sus validaciones URL existentes permanecen.
Las pruebas HTTP recorren b?squeda ? persistencia simulada ? detalle ? SourceProduct
? ObservationApiResolver sin FITS ni S3. La prueba no equivale a verificar PostgreSQL
real o el despliegue completo entre procesos.

## Validaci?n, errores y OpenAPI

S?lo se admiten los par?metros documentados. Fechas, detector en SIA, colecci?n en
TAP, page, offset y otros filtros no implementados devuelven 400. Tambi?n se rechazan
par?metros escalares repetidos, no finitos, geometr?a inv?lida y l?mites excesivos.

- 200: resultados, tambi?n count=0/results=[].
- 400: par?metros inv?lidos/no soportados o product_id mal formado.
- 404: producto temporal no descubierto/almacenado.
- 429: l?mite normal superado, con Retry-After.
- 502: IRSA falla, timeout o metadatos/referencia inv?lidos.
- 503: rate limiter Redis o infraestructura local de metadatos indisponible.

El throttle espec?fico de Observation mantiene 30/min SIA, 10/min TAP y 60/min detalle.
Si Redis falla, responde `{detail: "Rate limiting temporarily unavailable. Retry later."}`
con 503 antes de consultar IRSA; no habilita acceso ilimitado ni cambia otros servicios.
La pol?tica no garantiza disponibilidad sin Redis. Los fallos de cach? interna pueden
usar el fallback PostgreSQL existente, pero no evitan la necesidad del rate limiter.

OpenAPI en /api/v1/schema/ y Swagger en /api/v1/docs/ describen las envolventes reales,
unidades, errores e identidad del producto. La validaci?n 400 puede contener errores
por campo o detail; los errores operativos contienen detail.

## Validaci?n del 14 de noviembre de 2026

Pruebas deterministas, sin bases reales, Redis ni MinIO:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p smoke_stdlib.py
```

Resultados: **79 passed, 5 skipped**; smoke **5 passed**. Incluyen HTTP, validaci?n,
cache hit/miss, vac?os, deduplicaci?n, orden, ADQL seguro, SourceProduct/resolver,
503 Redis antes de IRSA, 429 normal y OpenAPI. La prueba de transporte intercepta
HTTPAdapter.send, atravesando Astroquery/PyVO/Requests reales, y comprueba el timeout.
OpenAPI tambi?n pas? validate/fail-on-warn sin advertencias, Django check sin
problemas, Ruff en los archivos revisados y git diff --check sin errores.

Integraci?n optativa s?lo de metadatos:

```powershell
$env:EON_RUN_LIVE_IRSA='1'
$env:IRSA_TIMEOUT_SECONDS='30'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/integration/test_irsa_live.py::test_sia_official_tutorial_coordinate tests/integration/test_irsa_live.py::test_tap_mvp_metadata_only
```

- SIA2 real: **pas?**. Adem?s la consulta DR1 exacta de arriba devolvi? 330 registros;
  [evidencia JSON](evidence/BE01_SIA_DR1.json) conserva consulta, instante y producto
  real de ejemplo. No se fija ese conteo como expectativa de pruebas.
- TAP real M101 D2 limit=5: **fall? por timeout de lectura a 30 s**; un ?ltimo intento
  con 60 s tambi?n fall? a los 60.38 s. Requests ReadTimeout fue convertido por PyVO
  en DALFormatError y por el adaptador en RuntimeError; la API traduce ese error a 502.
  El timeout funciona, pero no hay evidencia de recuperaci?n TAP real satisfactoria.
- No se ejecutaron sync, migraciones, pruebas S3, jobs ni descargas FITS de integraci?n.
  Las cinco integraciones optativas permanecen omitidas en la suite normal.

Para cerrar el MVP falta repetir TAP con ?xito y confirmar b?squeda/detalle con
persistencia del entorno de desarrollo. No reemplazar resultados TAP por datos ficticios.

## Integraci?n local Windows y BE-02/BE-03

La decisi?n del hackathon es MinIO independiente con imagen
`alpine/minio:RELEASE.2025-10-15T17-29-55Z`, fuera de Compose. Es una decisi?n de
configuraci?n, **no una imagen ni servicio verificados durante BE-01**.

Django nativo Windows usar? S3_ENDPOINT_URL=http://127.0.0.1:9000,
S3_ACCESS_KEY, S3_SECRET_KEY y S3_BUCKET. Se mantienen los puertos/contratos S3
existentes, sin dependencia espec?fica de esa imagen. .env.example refleja el endpoint
local, pero .env no se modific?. manage.py no carga .env autom?ticamente: exportar
variables en PowerShell o usar el cargador del entorno de desarrollo.

BE-01 no requiere MinIO, credenciales S3 ni bucket para ejecutar sus pruebas.
En ejecuci?n de la API s? requiere PostgreSQL propio y Redis para rate limiting.
No se cambi? compose.yaml; su topolog?a hist?rica no describe el despliegue independiente
acordado. Desde contenedores, 127.0.0.1 no apunta al host Windows: ajustar el endpoint
s?lo si cambia la forma de ejecuci?n.

Pr?ximos pasos:

1. BE-02: resolver product_id por HTTP, verificar URL/geometr?a, descargar FITS/cutout
   bajo demanda, validar sus cabeceras cient?ficas y preparar frames; almacenar FITS,
   frames y resultados en S3/MinIO cuando el servicio y bucket est?n verificados.
2. BE-03: coordinar jobs y publicar progreso/resultados para el frontend usando
   identificadores/referencias, sin transportar FITS en JSON o mensajes Celery.

Products asociados, Featured, filtros complejos, paginaci?n avanzada y redise?o
cient?fico de identidades quedan fuera de este MVP.
