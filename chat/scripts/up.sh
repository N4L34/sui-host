#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"
need_cmd docker python3
load_env
python3 "$ROOT/scripts/render_config.py"
if [[ "${ENABLE_CALLS:-false}" == "true" ]]; then
  ensure_call_dial
fi
compose up -d
if [[ "${ENABLE_CALLS:-false}" == "true" ]]; then
  ensure_livekit_published
fi
echo "stack is up on loopback. Publish it on the tailnet with: make serve"
