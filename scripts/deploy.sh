#!/bin/bash
# Runs on the local runner. Updates /opt/monitoring only.
set -euo pipefail
src=$(cd "$(dirname "$0")/.." && pwd)
dest=/opt/monitoring
sha=$(git -C "$src" rev-parse HEAD)

if git -C "$src" rev-parse --verify HEAD~1 >/dev/null 2>&1; then
  runtime=0
  while IFS= read -r path; do
    case "$path" in
      compose.yml | .env.example | platform/* | scripts/* | systemd/* | runner/*) runtime=1 ;;
    esac
  done < <(git -C "$src" diff --name-only HEAD~1 HEAD)
  if [ "$runtime" -eq 0 ]; then
    echo "docs only, containers stay"
    exit 0
  fi
fi

used=$(df -P / | awk 'NR==2 { gsub(/%/, "", $5); print $5 }')
if [ "$used" -ge 85 ]; then
  echo "disk ${used}% used, deploy refused"
  exit 1
fi
docker info >/dev/null
docker network inspect monitoring >/dev/null

backup=$(mktemp -d)
cp -a "$dest/compose.yml" "$dest/platform" "$dest/scripts" "$dest/systemd" "$backup/" 2>/dev/null || true

restore() {
  echo "restore previous monitoring files"
  if [ -f "$backup/compose.yml" ]; then
    cp -a "$backup/compose.yml" "$dest/compose.yml"
    rm -rf "$dest/platform" "$dest/scripts" "$dest/systemd"
    cp -a "$backup/platform" "$dest/platform"
    cp -a "$backup/scripts" "$dest/scripts"
    cp -a "$backup/systemd" "$dest/systemd"
    (cd "$dest" && docker compose up -d --wait) || true
  fi
}

rsync -a "$src/compose.yml" "$dest/compose.yml"
rsync -a "$src/.env.example" "$dest/.env.example"
rsync -a "$src/platform/" "$dest/platform/"
rsync -a "$src/scripts/" "$dest/scripts/"
rsync -a "$src/systemd/" "$dest/systemd/"

if [ -f "$dest/runtime/telegram_chat_id" ] && [ -f "$dest/runtime/telegram_token" ]; then
  chat=$(tr -d '[:space:]' < "$dest/runtime/telegram_chat_id")
  sed "s/__TELEGRAM_CHAT_ID__/${chat}/" "$dest/platform/alertmanager/alertmanager.yml.tmpl" > "$dest/runtime/alertmanager.yml.new"
  docker run --rm \
    -v "$dest/runtime/alertmanager.yml.new:/etc/alertmanager/alertmanager.yml:ro" \
    -v "$dest/platform/alertmanager/telegram.tmpl:/etc/alertmanager/telegram.tmpl:ro" \
    -v "$dest/runtime/telegram_token:/etc/alertmanager/telegram_token:ro" \
    --entrypoint amtool \
    prom/alertmanager:v0.28.0 \
    check-config /etc/alertmanager/alertmanager.yml
  mv "$dest/runtime/alertmanager.yml.new" "$dest/runtime/alertmanager.yml"
fi

if ! (cd "$dest" && docker compose up -d --wait); then
  restore
  exit 1
fi

smoke() {
  curl -fsS -o /dev/null http://127.0.0.1:3000/api/health
  curl -fsS -o /dev/null http://127.0.0.1:9091/-/ready
  curl -fsS -o /dev/null "https://sui.example.com/_matrix/client/versions"
}

if ! smoke; then
  restore
  exit 1
fi

text=/home/sui/tailnet-messenger/deploy/rendered/textfile
if [ -d "$text" ]; then
  printf 'monitoring_git_sha_info{sha="%s"} 1\n' "$sha" > "$text/monitoring_git_sha.prom"
fi
echo "deploy $sha ok"
