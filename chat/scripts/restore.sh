#!/usr/bin/env bash
# Replace the live server with a snapshot. Refuses to run without CONFIRM=yes.
# Usage: make restore CONFIRM=yes SNAPSHOT=backups/20261019T120000Z
source "$(dirname "$0")/lib.sh"
need_cmd docker python3
load_env

if [[ "${CONFIRM:-}" != "yes" ]]; then
  echo "restore refused. Re-run with CONFIRM=yes SNAPSHOT=backups/<stamp>" >&2
  exit 1
fi
if [[ -z "${SNAPSHOT:-}" || ! -d "$SNAPSHOT" ]]; then
  echo "SNAPSHOT must be a backup directory" >&2
  exit 1
fi
for part in postgres.dump synapse-data.tar secrets.tar; do
  if [[ ! -f "$SNAPSHOT/$part" ]]; then
    echo "snapshot is missing ${part}" >&2
    exit 1
  fi
done

echo "stopping the stack"
compose_down_all

echo "restoring secrets and rendered config"
tar -C "$ROOT" -xf "$SNAPSHOT/secrets.tar"

echo "restoring synapse data volume"
docker run --rm \
  -v svoi_synapse-data:/data \
  -v "$ROOT/$SNAPSHOT":/backup:ro \
  "${ALPINE_IMAGE:-alpine:3.22}" \
  sh -c 'find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} + && tar -C /data -xf /backup/synapse-data.tar'

echo "starting postgres"
compose up -d postgres
for _ in $(seq 1 30); do
  if compose exec -T postgres pg_isready -U synapse -d postgres >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "restoring database"
postgres_exec psql -U synapse -d postgres -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'synapse' AND pid <> pg_backend_pid();" \
  >/dev/null
postgres_exec dropdb -U synapse --if-exists --force synapse
postgres_exec createdb -U synapse -O synapse synapse
postgres_exec pg_restore -U synapse -d synapse --no-owner --clean --if-exists --exit-on-error < "$SNAPSHOT/postgres.dump"

echo "starting the stack"
compose up -d
echo "restored ${SNAPSHOT}. Run: make smoke"
echo "Friends may need to retry send. Sessions survive if this snapshot's macaroon secret matches the database."
