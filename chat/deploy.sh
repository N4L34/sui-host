#!/usr/bin/env bash
# Run the chat project's declared Compose stack after the active-call guard.
set -euo pipefail

if [ ! -f secrets/tailscale_ipv4 ]; then
  echo "missing secrets/tailscale_ipv4" >&2
  exit 1
fi
export TAILSCALE_IPV4
TAILSCALE_IPV4=$(tr -d '[:space:]' < secrets/tailscale_ipv4)

parts=$(curl -fsS --max-time 5 --get \
  --data-urlencode 'query=max(livekit_participant_total)' \
  http://127.0.0.1:9091/api/v1/query \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); r=d.get("data",{}).get("result",[]); print(r[0]["value"][1] if r else "0")')
if [ "$parts" != "0" ]; then
  echo "call has participants, chat containers stay"
  exit 0
fi

mapfile -t compose_files < <(python3 ../scripts/service_catalog.py --compose-files chat)
args=()
for file in "${compose_files[@]}"; do
  args+=(-f "$file")
done
docker compose --env-file .env "${args[@]}" up -d
