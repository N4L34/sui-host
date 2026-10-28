#!/usr/bin/env bash
# Local and GitHub checks. Does not start the homeserver.
source "$(dirname "$0")/lib.sh"

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
fi

bash "$ROOT/scripts/init.sh"
python3 -m py_compile scripts/render_config.py scripts/matrix_admin.py
python3 "$ROOT/scripts/render_config.py"

need_cmd shellcheck
shellcheck scripts/*.sh

if command -v yamllint >/dev/null 2>&1; then
  yamllint -c .yamllint.yaml deploy .github
else
  echo "yamllint is not installed; skipping"
fi

python3 - <<'PY'
import json
from pathlib import Path
text = Path("deploy/rendered/homeserver.yaml").read_text()
for needle in (
    "federation_domain_whitelist: []",
    "enable_registration: false",
    "url_preview_enabled: false",
    "encryption_enabled_by_default: true",
):
    if needle not in text:
        raise SystemExit(f"missing {needle}")
if "names: [federation]" in text or "names: [client, federation]" in text:
    raise SystemExit("federation listener rendered")
json.loads(Path("deploy/rendered/element-config.json").read_text())
print("render check ok")
PY

# Calls profile, in memory. Does not rewrite .env or deploy/rendered.
python3 - <<'PY'
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("render_config", "scripts/render_config.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

env = mod.read_env(Path(".env"))
env["ENABLE_CALLS"] = "true"
server = env["MATRIX_SERVER_NAME"].strip().rstrip(".")
homeserver = mod.homeserver(env, server)
for needle in ("names: [openid]", "/livekit/jwt"):
    if needle not in homeserver:
        raise SystemExit(f"calls render missing {needle}")
if "names: [federation]" in homeserver or "names: [client, federation]" in homeserver:
    raise SystemExit("federation listener in calls render")

livekit = mod.livekit_yaml("100.64.0.2", "ci-only")
for needle in (
    "use_ice_lite: true",
    "use_external_ip: false",
    "node_ip: 100.64.0.2",
    "port: 6789",
):
    if needle not in livekit:
        raise SystemExit(f"livekit yaml missing {needle}")

caddy = mod.caddyfile()
if "strip_prefix /livekit/sfu" not in caddy or "strip_prefix /livekit/jwt" not in caddy:
    raise SystemExit("calls caddy missing path prefixes")
dial = mod.caddyfile_dial()
if ":8448" not in dial or "reverse_proxy synapse:8008" not in dial:
    raise SystemExit("call-dial caddy missing")
print("calls render check ok")
PY

if command -v docker >/dev/null 2>&1; then
  mkdir -p deploy/rendered secrets
  # compose config wants the bind sources to exist. Real certs stay untouched.
  if [[ ! -e deploy/rendered/Caddyfile.dial ]]; then
    printf '%s\n' '# ci placeholder' > deploy/rendered/Caddyfile.dial
  fi
  if [[ ! -e secrets/tailscale.crt ]]; then
    printf '%s\n' 'ci-placeholder' > secrets/tailscale.crt
  fi
  if [[ ! -e secrets/tailscale.key ]]; then
    printf '%s\n' 'ci-placeholder' > secrets/tailscale.key
  fi
  docker compose --env-file .env -f deploy/compose.yml config >/dev/null
  docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.calls.yml config >/dev/null
  docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.observe.yml config >/dev/null
  docker compose --env-file .env \
    -f deploy/compose.yml \
    -f deploy/compose.calls.yml \
    -f deploy/compose.observe.yml \
    config >/dev/null
  echo "compose config ok"
else
  echo "docker is not installed; skipping compose config"
fi

echo "ci ok"
