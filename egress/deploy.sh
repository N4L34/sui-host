#!/bin/bash
# Explicit private preflight and bounded protocol smoke. Only this project.
set -euo pipefail
cd "$(dirname "$0")"
PYTHONPATH=.. python3 -m egress.install secrets/installation.json --destination . 2>/dev/null || {
  echo 'egress private configuration required' >&2
  exit 1
}

docker compose config --quiet
# A first failed rollout must stop only the new egress containers. Existing
# installation failures keep the deny policy; private/image rollback is manual.
existing=$(docker compose ps -q)
if ! docker compose up --build -d || ! docker compose exec -T status python3 -m egress.smoke; then
  if [ -z "$existing" ]; then docker compose stop; fi
  echo 'egress deployment failed; preserve host deny policy' >&2
  exit 1
fi
