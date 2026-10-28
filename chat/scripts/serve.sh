#!/usr/bin/env bash
# Point Tailscale HTTPS at the loopback listeners. Nothing is opened on the public NIC.
source "$(dirname "$0")/lib.sh"
need_cmd tailscale
load_env

if [[ -z "${MATRIX_SERVER_NAME:-}" ]]; then
  echo "MATRIX_SERVER_NAME is empty" >&2
  exit 1
fi

tailscale serve --bg --https=8443 "http://127.0.0.1:8080"

if [[ "${ENABLE_CALLS:-false}" == "true" ]]; then
  tailscale serve --bg --https=8444 "http://127.0.0.1:7882"
  echo "https://${MATRIX_SERVER_NAME}:8444       calls"
fi

grafana_code="000"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  grafana_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:3000/login || true)"
  if [[ -n "$grafana_code" && "$grafana_code" != "000" ]]; then
    break
  fi
  sleep 2
done
if [[ -n "$grafana_code" && "$grafana_code" != "000" ]]; then
  # The bare name is the dashboard. Serve drops the mount path, so each
  # Matrix prefix is put back on the Synapse target. Chat stays on 443.
  tailscale serve --bg --https=443 "http://127.0.0.1:3000"
  tailscale serve --bg --https=443 --set-path=/_matrix "http://127.0.0.1:8008/_matrix"
  tailscale serve --bg --https=443 --set-path=/_synapse "http://127.0.0.1:8008/_synapse"
  tailscale serve --bg --https=443 --set-path=/.well-known "http://127.0.0.1:8008/.well-known"
  tailscale serve --https=3443 off >/dev/null 2>&1 || true
  echo "https://${MATRIX_SERVER_NAME}            Grafana"
  echo "https://${MATRIX_SERVER_NAME}/_matrix    Synapse"
else
  tailscale serve --bg --https=443 "http://127.0.0.1:8008"
  echo "https://${MATRIX_SERVER_NAME}            Synapse"
fi

echo "https://${MATRIX_SERVER_NAME}:8443       Element Web"
tailscale serve status
