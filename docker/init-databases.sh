#!/bin/sh
set -eu
for database in eon_image_processing eon_spectral eon_jobs; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -c "CREATE DATABASE $database"
done
