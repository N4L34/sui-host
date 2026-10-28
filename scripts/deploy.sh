#!/bin/bash
# Runs on the local runner. Updates /home/sui/sui-host and starts only a changed service folder.
set -euo pipefail
src=$(cd "$(dirname "$0")/.." && pwd)
dest=/home/sui/sui-host
sha=$(git -C "$src" rev-parse HEAD)

runtime=0
services=()
if git -C "$src" rev-parse --verify HEAD~1 >/dev/null 2>&1; then
  while IFS= read -r path; do
    case "$path" in
      monitoring/* | scripts/* | systemd/* | runner/*) runtime=1; services+=(monitoring) ;;
      commands/*) runtime=1; services+=(commands) ;;
      avtobot/*) runtime=1; services+=(avtobot) ;;
      screen/*) runtime=1; services+=(screen) ;;
      chat/*) runtime=1; services+=(chat) ;;
    esac
  done < <(git -C "$src" diff --name-only HEAD~1 HEAD)
else
  runtime=1
  services=(monitoring commands avtobot screen chat)
fi

if [ "$runtime" -eq 0 ]; then
  echo "docs only, containers stay"
  exit 0
fi

used=$(df -P / | awk 'NR==2 { gsub(/%/, "", $5); print $5 }')
if [ "$used" -ge 85 ]; then
  echo "disk ${used}% used, deploy refused"
  exit 1
fi
docker info >/dev/null
docker network inspect monitoring >/dev/null

uniq=()
for name in "${services[@]}"; do
  skip=0
  for have in "${uniq[@]+"${uniq[@]}"}"; do
    if [ "$have" = "$name" ]; then
      skip=1
    fi
  done
  if [ "$skip" -eq 0 ]; then
    uniq+=("$name")
  fi
done

backup=$(mktemp -d)
for name in "${uniq[@]}"; do
  mkdir -p "$backup/$name"
  if [ -d "$dest/$name" ]; then
    if [ "$name" = chat ]; then
      rsync -a --exclude 'secrets/' --exclude '.env' --exclude 'backups/' --exclude 'deploy/rendered/' "$dest/$name/" "$backup/$name/"
    else
      cp -a "$dest/$name/." "$backup/$name/"
    fi
  fi
done

compose_up() {
  local name=$1
  if [ "$name" != chat ]; then
    (cd "$dest/$name" && docker compose up -d)
    return
  fi
  local parts
  parts=$(curl -fsS --max-time 5 --get --data-urlencode 'query=max(livekit_participant_total)' http://127.0.0.1:9091/api/v1/query \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); r=d.get("data",{}).get("result",[]); print(r[0]["value"][1] if r else "0")')
  if [ "$parts" != "0" ]; then
    echo "call has participants, chat containers stay"
    return 0
  fi
  (
    cd "$dest/chat"
    export TAILSCALE_IPV4
    TAILSCALE_IPV4=$(tr -d '[:space:]' < secrets/tailscale_ipv4)
    docker compose --env-file .env \
      -f deploy/compose.yml \
      -f deploy/compose.calls.yml \
      -f deploy/rendered/compose.dial.yml \
      -f deploy/compose.platform-net.yml \
      up -d
  )
}

restore() {
  echo "restore previous service files"
  for name in "${uniq[@]}"; do
    if [ -d "$backup/$name" ]; then
      mkdir -p "$dest/$name"
      cp -a "$backup/$name/." "$dest/$name/"
      compose_up "$name" || true
    fi
  done
}

rsync -a \
  --exclude '.git/' \
  --exclude 'monitoring/.env' \
  --exclude 'monitoring/runtime/' \
  --exclude 'secrets/' \
  "$src/monitoring/" "$dest/monitoring/"
rsync -a --exclude 'secrets/' "$src/commands/" "$dest/commands/"
rsync -a --exclude 'secrets/' "$src/avtobot/" "$dest/avtobot/"
rsync -a --exclude 'secrets/' "$src/screen/" "$dest/screen/"
rsync -a \
  --exclude '.env' \
  --exclude 'secrets/' \
  --exclude 'backups/' \
  --exclude 'deploy/rendered/' \
  --exclude 'deploy/observe/alertmanager/bot_token' \
  "$src/chat/" "$dest/chat/"
rsync -a "$src/scripts/" "$dest/scripts/"
rsync -a "$src/systemd/" "$dest/systemd/"
rsync -a "$src/runner/" "$dest/runner/"

if [ -f "$dest/monitoring/runtime/telegram_chat_id" ] && [ -f "$dest/monitoring/runtime/telegram_token" ]; then
  chat=$(tr -d '[:space:]' < "$dest/monitoring/runtime/telegram_chat_id")
  sed "s/__TELEGRAM_CHAT_ID__/${chat}/" "$dest/monitoring/platform/alertmanager/alertmanager.yml.tmpl" > "$dest/monitoring/runtime/alertmanager.yml.new"
  docker run --rm \
    -v "$dest/monitoring/runtime/alertmanager.yml.new:/etc/alertmanager/alertmanager.yml:ro" \
    -v "$dest/monitoring/platform/alertmanager/telegram.tmpl:/etc/alertmanager/telegram.tmpl:ro" \
    -v "$dest/monitoring/runtime/telegram_token:/etc/alertmanager/telegram_token:ro" \
    --entrypoint amtool \
    prom/alertmanager:v0.28.0 \
    check-config /etc/alertmanager/alertmanager.yml
  mv "$dest/monitoring/runtime/alertmanager.yml.new" "$dest/monitoring/runtime/alertmanager.yml"
fi

for name in "${uniq[@]}"; do
  if ! compose_up "$name"; then
    restore
    exit 1
  fi
done

if printf '%s\n' "${uniq[@]}" | grep -qx monitoring; then
  smoke() {
    curl -fsS -o /dev/null http://127.0.0.1:3000/api/health
    curl -fsS -o /dev/null http://127.0.0.1:9091/-/ready
    curl -fsS -o /dev/null "https://sui.example.com/_matrix/client/versions"
  }
  if ! smoke; then
    restore
    exit 1
  fi
fi

text=/home/sui/sui-host/chat/deploy/rendered/textfile
if [ -d "$text" ]; then
  printf 'monitoring_git_sha_info{sha="%s"} 1\n' "$sha" > "$text/monitoring_git_sha.prom"
fi
echo "deploy $sha ok"
