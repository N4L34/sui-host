#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s egress/tests -v
docker compose -f egress/compose.yml config --format json | python3 scripts/policy.py
docker build -t sui-egress:test egress
docker run --rm --network none --entrypoint /usr/local/bin/xray sui-egress:test version
