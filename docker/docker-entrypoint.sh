#!/bin/sh
# Container entrypoint: optionally migrate/seed, then exec the API process.
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "Applying database migrations..."
    alembic upgrade head
fi

if [ "${RUN_SEED:-false}" = "true" ]; then
    echo "Seeding demo data..."
    python -m scripts.seed
fi

exec "$@"
