#!/bin/bash
# Runs on the local runner. Syncs managed files and deploys changed projects only.
set -euo pipefail
src=$(cd "$(dirname "$0")/.." && pwd)
dest=/home/sui/sui-host
sha=$(git -C "$src" rev-parse HEAD)

paths=()
if git -C "$src" rev-parse --verify HEAD~1 >/dev/null 2>&1; then
  mapfile -t paths < <(git -C "$src" diff --name-only HEAD~1 HEAD)
else
  mapfile -t paths < <(git -C "$src" ls-tree -r --name-only HEAD)
fi
mapfile -t services < <(printf '%s\n' "${paths[@]}" | python3 "$src/scripts/service_catalog.py" --changed)
common_changed=0
systemd_changed=0
runner_changed=0
for path in "${paths[@]}"; do
  case "$path" in
    scripts/*) common_changed=1 ;;
    systemd/*) common_changed=1; systemd_changed=1 ;;
    runner/*) runner_changed=1 ;;
  esac
done

if [ "${#services[@]}" -eq 0 ] && [ "$common_changed" -eq 0 ] && [ "$runner_changed" -eq 0 ]; then
  echo "no deployable project or host configuration changed"
  exit 0
fi

used=$(df -P / | awk 'NR==2 { gsub(/%/, "", $5); print $5 }')
if [ "$used" -ge 85 ]; then
  echo "disk ${used}% used, deploy refused"
  exit 1
fi
if [ "${#services[@]}" -gt 0 ]; then
  docker info >/dev/null
  docker network inspect monitoring >/dev/null
fi
if printf '%s\n' "${services[@]}" | grep -qx commands; then
  python3 "$src/scripts/migrate_runtime_env.py" \
    "$dest/commands/runtime.env" /home/sui/commands/runtime.env
fi

backup=$(mktemp -d)
declare -A had_service=()
had_alertmanager_config=0
managed_excludes=(
  --exclude='.git/'
  --exclude='secrets/'
  --exclude='.env'
  --exclude='runtime.env'
  --exclude='backups/'
  --exclude='deploy/rendered/'
  --exclude='runtime/'
  --exclude='deploy/observe/alertmanager/bot_token'
)
for name in "${services[@]}"; do
  mkdir -p "$backup/$name"
  had_service[$name]=0
  if [ -d "$dest/$name" ]; then
    had_service[$name]=1
    rsync -a --checksum "${managed_excludes[@]}" "$dest/$name/" "$backup/$name/"
  fi
done
for name in scripts systemd; do
  if [ -d "$dest/$name" ]; then
    mkdir -p "$backup/$name"
    rsync -a --checksum "$dest/$name/" "$backup/$name/"
  fi
done
if [ "$runner_changed" -eq 1 ] && [ -d "$dest/runner" ]; then
  mkdir -p "$backup/runner"
  rsync -a --checksum --exclude='runner_token' "$dest/runner/" "$backup/runner/"
fi
if [ -f "$dest/monitoring/runtime/alertmanager.yml" ]; then
  had_alertmanager_config=1
  mkdir -p "$backup/monitoring-runtime"
  cp -p "$dest/monitoring/runtime/alertmanager.yml" "$backup/monitoring-runtime/alertmanager.yml"
fi

compose_up() {
  local name=$1 hook
  hook=$(python3 "$dest/scripts/service_catalog.py" --deploy-hook "$name")
  if [ -n "$hook" ]; then
    (cd "$dest/$name" && "./$hook")
  else
    (cd "$dest/$name" && docker compose up -d)
  fi
}
attempted=()

restore() {
  local name
  echo "restore previous managed files"
  for name in "${services[@]}"; do
    mkdir -p "$dest/$name"
    rsync -a --delete --checksum "${managed_excludes[@]}" "$backup/$name/" "$dest/$name/"
  done
  for name in scripts systemd; do
    if [ -d "$backup/$name" ]; then
      rsync -a --delete --checksum "$backup/$name/" "$dest/$name/"
    fi
  done
  if [ "$runner_changed" -eq 1 ] && [ -d "$backup/runner" ]; then
    rsync -a --delete --checksum --exclude='runner_token' "$backup/runner/" "$dest/runner/"
  fi
  if [ "$had_alertmanager_config" -eq 1 ]; then
    cp -p "$backup/monitoring-runtime/alertmanager.yml" "$dest/monitoring/runtime/alertmanager.yml"
  elif [ -f "$dest/monitoring/runtime/alertmanager.yml" ]; then
    rm -f "$dest/monitoring/runtime/alertmanager.yml"
  fi
  if [ "$systemd_changed" -eq 1 ]; then
    systemctl daemon-reload || true
  fi
  for name in "${attempted[@]}"; do
    if [ "${had_service[$name]}" -eq 1 ]; then
      compose_up "$name" || true
    fi
  done
}

for name in "${services[@]}"; do
  if ! rsync -a --delete --checksum "${managed_excludes[@]}" "$src/$name/" "$dest/$name/"; then
    restore
    exit 1
  fi
done
if [ "$common_changed" -eq 1 ] || [ "${#services[@]}" -gt 0 ]; then
  if ! rsync -a --delete --checksum "$src/scripts/" "$dest/scripts/" \
    || ! rsync -a --delete --checksum "$src/systemd/" "$dest/systemd/"; then
    restore
    exit 1
  fi
fi
if [ "$runner_changed" -eq 1 ]; then
  if ! rsync -a --delete --checksum --exclude='runner_token' "$src/runner/" "$dest/runner/"; then
    restore
    exit 1
  fi
fi
if [ "$systemd_changed" -eq 1 ]; then
  if ! systemctl daemon-reload; then
    restore
    exit 1
  fi
fi

if [ -f "$dest/monitoring/runtime/telegram_chat_id" ] && [ -f "$dest/monitoring/runtime/telegram_token" ]; then
  if ! chat=$(tr -d '[:space:]' < "$dest/monitoring/runtime/telegram_chat_id"); then
    restore
    exit 1
  fi
  if ! sed "s/__TELEGRAM_CHAT_ID__/${chat}/" "$dest/monitoring/platform/alertmanager/alertmanager.yml.tmpl" > "$dest/monitoring/runtime/alertmanager.yml.new"; then
    restore
    exit 1
  fi
  if ! docker run --rm \
    -v "$dest/monitoring/runtime/alertmanager.yml.new:/etc/alertmanager/alertmanager.yml:ro" \
    -v "$dest/monitoring/platform/alertmanager/telegram.tmpl:/etc/alertmanager/telegram.tmpl:ro" \
    -v "$dest/monitoring/runtime/telegram_token:/etc/alertmanager/telegram_token:ro" \
    --entrypoint amtool \
    prom/alertmanager:v0.28.0 \
    check-config /etc/alertmanager/alertmanager.yml; then
    restore
    exit 1
  fi
  if ! mv "$dest/monitoring/runtime/alertmanager.yml.new" "$dest/monitoring/runtime/alertmanager.yml"; then
    restore
    exit 1
  fi
fi
for name in "${services[@]}"; do
  attempted+=("$name")
  if ! compose_up "$name"; then
    restore
    exit 1
  fi
done

if printf '%s\n' "${services[@]}" | grep -qx monitoring; then
  smoke() {
    local matrix_server_name
    matrix_server_name=$(python3 - "$dest/chat/.env" <<'PY'
from pathlib import Path
import sys
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line.startswith("MATRIX_SERVER_NAME="):
        print(line.split("=", 1)[1].strip().strip("\"'"))
        break
PY
    )
    [ -n "$matrix_server_name" ] || return 1
    curl -fsS -o /dev/null http://127.0.0.1:3000/api/health
    curl -fsS -o /dev/null http://127.0.0.1:9091/-/ready
    curl -fsS -o /dev/null "https://${matrix_server_name}/_matrix/client/versions"
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
if [ "$runner_changed" -eq 1 ]; then
  echo "runner configuration synced; runner container was not restarted by its active job"
fi
echo "deploy $sha ok"
