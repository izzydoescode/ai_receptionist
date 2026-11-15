#!/bin/sh
# Start the bear:ci image and check it the way Fly will: health check, /health, non-root, and secrets enforced.
# Usage: container_smoke.sh <container-name> [DATABASE_URL]
set -eu
name=$1
database_url=${2:-}
set -- --name "$name" --network host --env-file "$RUNNER_TEMP/bear.env" \
    --health-interval 2s --health-start-period 5s --health-retries 10
if [ "$database_url" ]; then
    set -- "$@" -e DATABASE_URL="$database_url"
fi
docker rm -f "$name" >/dev/null 2>&1 || true
docker run -d "$@" bear:ci >/dev/null

# The image's own HEALTHCHECK has to report healthy.
status=starting
for _ in $(seq 1 45); do
    status=$(docker inspect --format '{{.State.Health.Status}}' "$name")
    [ "$status" = healthy ] && break
    [ "$(docker inspect --format '{{.State.Running}}' "$name")" = true ] || break
    sleep 2
done
echo "health: $status"
[ "$status" = healthy ]

curl -fsS http://127.0.0.1:8080/health | python3 -c 'import json, sys; assert json.load(sys.stdin)["ok"] is True'
[ "$(docker exec "$name" id -u)" = 10001 ]
# Tool routes must reject a request without the shared secret.
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8080/tools/take_message -H 'Content-Type: application/json' -d '{}')
echo "tool call without secret: $code"
[ "$code" = 401 ]
docker rm -f "$name" >/dev/null
echo "ok: $name"
