#!/bin/bash
# Move metrics, logs, and alerts from the chat project to project sui.
# Does not recreate chat containers and does not restart svoi.service.
set -euo pipefail
cd /home/sui/sui-host

apply=0
if [[ "${1:-}" == "--apply" ]]; then
  apply=1
fi

chat_ok() {
  curl -fsS --max-time 5 http://127.0.0.1:8008/_matrix/client/versions | grep -q '"versions"'
}

if ! chat_ok; then
  echo "chat is not answering, abort" >&2
  exit 1
fi

started="$(docker inspect -f '{{.State.StartedAt}}' svoi-livekit-1)"
echo "livekit $started"

for volume in svoi_prom-data svoi_alert-data svoi_loki-data svoi_alloy-data svoi_grafana-data; do
  docker volume inspect "$volume" >/dev/null
done
docker network inspect svoi_data >/dev/null

if [[ ! -f .env ]]; then
  echo ".env is missing" >&2
  exit 1
fi

docker network create sui >/dev/null 2>&1 || true

connect_net() {
  local alias="$1" name="$2"
  docker inspect "$name" >/dev/null
  if docker inspect -f '{{json .NetworkSettings.Networks}}' "$name" | grep -q '"sui"'; then
    echo "already on sui: $name"
    return
  fi
  docker network connect --alias "$alias" sui "$name"
  echo "connected $name as $alias"
}

connect_net synapse svoi-synapse-1
connect_net element-web svoi-element-web-1
connect_net livekit svoi-livekit-1
connect_net lk-jwt svoi-lk-jwt-1
connect_net calls-proxy svoi-calls-proxy-1

mkdir -p runtime/file_sd
src_yml=/home/sui/tailnet-messenger/deploy/rendered/alertmanager.yml
src_token=/home/sui/tailnet-messenger/deploy/observe/alertmanager/bot_token
if [[ ! -s "$src_yml" || ! -s "$src_token" ]]; then
  echo "alertmanager files missing" >&2
  exit 1
fi
install -m 644 "$src_yml" runtime/alertmanager.yml
install -m 644 "$src_token" runtime/telegram_token
python3 scripts/discover.py

if ! chat_ok; then
  echo "chat dropped during network connect" >&2
  exit 1
fi
now="$(docker inspect -f '{{.State.StartedAt}}' svoi-livekit-1)"
if [[ "$now" != "$started" ]]; then
  echo "livekit start time changed" >&2
  exit 1
fi

docker compose config >/dev/null
echo "compose config ok, livekit still $now"

if [[ "$apply" -ne 1 ]]; then
  echo "dry-run done"
  exit 0
fi

old=(
  svoi-grafana-1
  svoi-prometheus-1
  svoi-alertmanager-1
  svoi-loki-1
  svoi-alloy-1
  svoi-cadvisor-1
  svoi-blackbox-1
  svoi-node-exporter-1
  svoi-postgres-exporter-1
)

rollback() {
  echo "rolling back old observe containers" >&2
  docker compose stop >/dev/null 2>&1 || true
  for name in "${old[@]}"; do
    docker start "$name" >/dev/null 2>&1 || true
  done
}

docker stop "${old[@]}"
if ! docker compose up -d; then
  rollback
  exit 1
fi

probe_ok() {
  curl -fsS --max-time 3 -G 'http://127.0.0.1:9091/api/v1/query' \
    --data-urlencode 'query=min(probe_success{job="sui-probe",service="synapse"})' \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); r=d["data"]["result"]; raise SystemExit(0 if r and float(r[0]["value"][1])==1 else 1)'
}

ok=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18; do
  if curl -fsS --max-time 3 http://127.0.0.1:3000/api/health >/dev/null \
    && curl -fsS --max-time 3 http://127.0.0.1:9091/-/ready >/dev/null \
    && chat_ok \
    && probe_ok; then
    ok=1
    break
  fi
  sleep 5
done
if [[ "$ok" -ne 1 ]]; then
  echo "platform did not become ready" >&2
  rollback
  exit 1
fi

now="$(docker inspect -f '{{.State.StartedAt}}' svoi-livekit-1)"
if [[ "$now" != "$started" ]]; then
  echo "livekit start time changed after up" >&2
  exit 1
fi

for name in "${old[@]}"; do
  docker rm "$name" >/dev/null
  echo "removed $name"
done

echo "cutover done, livekit $now"
