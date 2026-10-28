#!/usr/bin/env bash
# Hourly-style backup. Media and the signing key are copied first, then pg_dump.
# restic runs only when RESTIC_REPOSITORY is set. Local copies are not an offsite backup.
source "$(dirname "$0")/lib.sh"
need_cmd docker python3
load_env

if [[ ! -f deploy/rendered/homeserver.yaml ]]; then
  echo "rendered config is missing. Run: make render" >&2
  exit 1
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="backups/${stamp}"
mkdir -p "$dest"
chmod 700 backups "$dest"

echo "copying synapse data (signing key and media)"
docker run --rm \
  -v svoi_synapse-data:/data:ro \
  -v "$ROOT/$dest":/backup \
  "${ALPINE_IMAGE:-alpine:3.22}" \
  sh -c "tar -C /data -cf /backup/synapse-data.tar . && chown $(id -u):$(id -g) /backup/synapse-data.tar && chmod 600 /backup/synapse-data.tar"

echo "dumping postgres"
postgres_exec pg_dump -U synapse -d synapse -Fc > "$dest/postgres.dump"

tar -C "$ROOT" -cf "$dest/secrets.tar" secrets .env deploy/rendered/homeserver.yaml deploy/rendered/element-config.json
chmod 600 "$dest/postgres.dump" "$dest/secrets.tar" "$dest/synapse-data.tar"

cat > "$dest/meta.txt" <<EOF
created_utc=${stamp}
server_name=${MATRIX_SERVER_NAME}
synapse_image=${SYNAPSE_IMAGE}
postgres_image=${POSTGRES_IMAGE}
EOF

# Keep two weeks of local snapshots before the offsite copy.
# A restic failure must not hide the fact that the local copy exists.
python3 - <<'PY'
import pathlib
root = pathlib.Path("backups")
dirs = sorted(p for p in root.iterdir() if p.is_dir())
for old in dirs[:-14]:
    for child in old.rglob("*"):
        if child.is_file():
            child.unlink()
    for child in sorted(old.rglob("*"), reverse=True):
        if child.is_dir():
            child.rmdir()
    old.rmdir()
PY

offsite=0
restic_failed=0
if [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  need_cmd restic
  export RESTIC_PASSWORD_FILE="$ROOT/secrets/restic_password"
  if ! restic cat config >/dev/null 2>&1; then
    echo "restic repository is not initialized. Run: make restic-init" >&2
    restic_failed=1
  elif restic backup "$dest" \
    && restic forget --keep-hourly 24 --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --prune; then
    offsite=1
  else
    echo "restic backup failed. The local snapshot is still in ${dest}." >&2
    restic_failed=1
  fi
else
  echo "RESTIC_REPOSITORY is empty. This backup stays on the same disk as the server."
fi

mkdir -p deploy/rendered/textfile
now="$(date +%s)"
prom="deploy/rendered/textfile/backup.prom"
previous_offsite=""
if [[ "$offsite" -ne 1 && -f "$prom" ]]; then
  previous_offsite="$(grep '^messenger_last_offsite_backup_success_timestamp ' "$prom" || true)"
fi
tmp="${prom}.tmp"
{
  echo "# HELP messenger_last_backup_success_timestamp Unix time of the last local backup."
  echo "# TYPE messenger_last_backup_success_timestamp gauge"
  echo "messenger_last_backup_success_timestamp ${now}"
  if [[ "$offsite" -eq 1 ]]; then
    echo "# HELP messenger_last_offsite_backup_success_timestamp Unix time of the last restic backup."
    echo "# TYPE messenger_last_offsite_backup_success_timestamp gauge"
    echo "messenger_last_offsite_backup_success_timestamp ${now}"
  elif [[ -n "$previous_offsite" ]]; then
    echo "# HELP messenger_last_offsite_backup_success_timestamp Unix time of the last restic backup."
    echo "# TYPE messenger_last_offsite_backup_success_timestamp gauge"
    echo "$previous_offsite"
  fi
} > "$tmp"
mv "$tmp" "$prom"
echo "backup written to ${dest}"
if [[ "$restic_failed" -eq 1 ]]; then
  exit 1
fi
