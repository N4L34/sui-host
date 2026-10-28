#!/usr/bin/env bash
# Prove the newest pg_dump opens. Does not touch the live database.
source "$(dirname "$0")/lib.sh"
need_cmd docker python3
load_env

snap="$(find backups -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"
if [[ -z "$snap" || ! -f "$snap/postgres.dump" ]]; then
  echo "no local backup to drill. Run: make backup" >&2
  exit 1
fi

name="svoi-drill-$$"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "drilling ${snap}"
docker run -d --name "$name" \
  -e POSTGRES_PASSWORD=drill \
  -e POSTGRES_USER=synapse \
  -e POSTGRES_DB=synapse \
  "${POSTGRES_IMAGE}" >/dev/null

for _ in $(seq 1 30); do
  if docker exec "$name" pg_isready -U synapse -d synapse >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

docker exec -i "$name" pg_restore -U synapse -d synapse --no-owner --clean --if-exists --exit-on-error < "$snap/postgres.dump"
count="$(docker exec "$name" psql -U synapse -d synapse -tA -c 'SELECT count(*) FROM users;')"
if [[ ! "$count" =~ ^[0-9]+$ ]]; then
  echo "drill failed: users table did not restore" >&2
  exit 1
fi
echo "drill ok: ${count} users in ${snap}"
