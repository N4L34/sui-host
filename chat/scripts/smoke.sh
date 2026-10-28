#!/usr/bin/env bash
# Acceptance checks for a running core stack. Exits non-zero on failure.
source "$(dirname "$0")/lib.sh"
need_cmd curl python3 docker
load_env

fail=0
check() {
  local name="$1"
  shift
  if "$@"; then
    echo "ok  ${name}"
  else
    echo "FAIL ${name}" >&2
    fail=1
  fi
}

check "synapse /health" curl -fsS --max-time 10 http://127.0.0.1:8008/health
check "client versions" curl -fsS --max-time 10 http://127.0.0.1:8008/_matrix/client/versions
check "element web" curl -fsS --max-time 10 -o /dev/null http://127.0.0.1:8080/

reg_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
  -X POST http://127.0.0.1:8008/_matrix/client/v3/register \
  -H 'content-type: application/json' \
  -d '{"username":"smoke-should-fail","password":"not-a-real-password"}' || true)"
check "registration closed (got ${reg_code})" test "$reg_code" != "200" -a "$reg_code" != "000"

binding="$(compose port synapse 8008 || true)"
if [[ "$binding" == 127.0.0.1:* ]]; then
  echo "ok  synapse bound to loopback (${binding})"
else
  echo "FAIL synapse bound to loopback (${binding:-missing})" >&2
  fail=1
fi

# The image EXPOSEs 8448. That shows up as "8448/tcp" and is not a host publish.
# A real publish looks like "0.0.0.0:8448->8448/tcp". call-dial listens on 8448
# only inside the compose network, so it must not appear as a host mapping.
if compose ps --format '{{.Service}} {{.Ports}}' | grep -E -- '->8448/' >/dev/null; then
  echo "FAIL federation port 8448 is published" >&2
  fail=1
else
  echo "ok  federation port 8448 is not published"
fi

python3 - <<'PY'
from pathlib import Path
text = Path("deploy/rendered/homeserver.yaml").read_text()
need = [
    "federation_domain_whitelist: []",
    "enable_registration: false",
    "url_preview_enabled: false",
]
missing = [item for item in need if item not in text]
if "federation]" in text and "names: [client]" not in text:
    raise SystemExit("federation listener present")
if missing:
    raise SystemExit("rendered homeserver missing: " + ", ".join(missing))
print("ok  rendered homeserver keeps federation and registration closed")
PY

wk_code="$(curl -sS -o /tmp/svoi-wellknown.json -w '%{http_code}' --max-time 10 \
  http://127.0.0.1:8008/.well-known/matrix/client || true)"
if [[ "$wk_code" == "200" ]] && grep -q '"m.homeserver"' /tmp/svoi-wellknown.json; then
  echo "ok  well-known client document"
else
  echo "FAIL well-known client document (HTTP ${wk_code})" >&2
  fail=1
fi
rm -f /tmp/svoi-wellknown.json

if [[ "$fail" -ne 0 ]]; then
  echo "smoke failed" >&2
  exit 1
fi
echo "smoke passed"
