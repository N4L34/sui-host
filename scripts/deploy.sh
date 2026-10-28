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
      chat/*) ;;
    esac
  done < <(git -C "$src" diff --name-only HEAD~1 HEAD)
else
  runtime=1
  services=(monitoring commands avtobot)
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
    cp -a "$dest/$name/." "$backup/$name/"
  fi
done

restore() {
  echo "restore previous service files"
  for name in "${uniq[@]}"; do
    if [ -d "$backup/$name" ]; then
      mkdir -p "$dest/$name"
      cp -a "$backup/$name/." "$dest/$name/"
      (cd "$dest/$name" && docker compose up -d) || true
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
  if ! (cd "$dest/$name" && docker compose up -d); then
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

text=/home/sui/tailnet-messenger/deploy/rendered/textfile
if [ -d "$text" ]; then
  printf 'monitoring_git_sha_info{sha="%s"} 1\n' "$sha" > "$text/monitoring_git_sha.prom"
fi
echo "deploy $sha ok"
