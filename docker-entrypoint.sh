#!/bin/sh
set -eu
# Fly runs migrations as a release command. For a plain `docker run`, set BEAR_MIGRATE_ON_BOOT=1.
if [ "${BEAR_MIGRATE_ON_BOOT:-0}" = "1" ]; then
    alembic upgrade head
fi
exec "$@"
