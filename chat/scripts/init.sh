#!/usr/bin/env bash
# Create .env and the secret files. Existing secrets are left alone.
source "$(dirname "$0")/lib.sh"
need_cmd python3

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "created .env from .env.example"
fi

mkdir -p secrets
chmod 700 secrets

secret_file secrets/postgres_password
secret_file secrets/registration_shared_secret
secret_file secrets/macaroon_secret
secret_file secrets/form_secret
secret_file secrets/grafana_admin_password
secret_file secrets/restic_password
secret_file secrets/livekit_secret
python3 - <<'PY'
from pathlib import Path
secret = Path("secrets/livekit_secret").read_text(encoding="utf-8").strip()
path = Path("secrets/calls.env")
path.write_text(f'LIVEKIT_SECRET="{secret}"\n', encoding="utf-8")
path.chmod(0o600)
PY

if command -v tailscale >/dev/null 2>&1; then
  ip="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "$ip" > secrets/tailscale_ipv4
    chmod 600 secrets/tailscale_ipv4
  fi
  dns="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys
try:
    data=json.load(sys.stdin)
except Exception:
    sys.exit(0)
name=data.get("Self",{}).get("DNSName","")
print(name.rstrip("."))' || true)"
  if [[ -n "${dns}" ]]; then
    echo "this node's MagicDNS name: ${dns}"
    echo "MATRIX_SERVER_NAME in .env must be exactly that before the first account."
  fi
else
  echo "tailscale is not installed yet. Install it before make serve."
fi

echo "secrets are in secrets/ (mode 600). They are gitignored."
echo "next: edit .env, then make render && make up && make serve"
