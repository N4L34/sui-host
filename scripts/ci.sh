#!/bin/bash
# Same checks locally and in GitHub Actions. Does not touch the node.
set -euo pipefail
cd "$(dirname "$0")/.."
root=$PWD

python3 scripts/discover.py --self-test

python3 - <<'PY'
from pathlib import Path
env = Path(".env.example").read_text(encoding="utf-8")
docs = Path("docs/services.md").read_text(encoding="utf-8")
missing = []
for line in env.splitlines():
    if not line or line.startswith("#") or "_IMAGE=" not in line:
        continue
    image = line.split("=", 1)[1].strip()
    if image not in docs:
        missing.append(image)
if missing:
    raise SystemExit("нет в docs/services.md: " + ", ".join(missing))
print("inventory tags ok")
PY

set -a
# shellcheck disable=SC2163
while IFS= read -r line; do
  case "$line" in
    ""|\#*) continue ;;
  esac
  key=${line%%=*}
  value=${line#*=}
  export "$key=$value"
done < .env.example
set +a

rendered=$(mktemp)
docker compose --env-file .env.example config --format json > "$rendered"
python3 scripts/policy.py < "$rendered"
rm -f "$rendered"

sd=$(mktemp -d)
printf '[]\n' > "$sd/probes.json"
printf '[]\n' > "$sd/metrics.json"
docker run --rm \
  -v "$root/platform/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  -v "$root/platform/prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro" \
  -v "$sd:/etc/prometheus/file_sd:ro" \
  --entrypoint promtool \
  prom/prometheus:v3.4.0 \
  check config /etc/prometheus/prometheus.yml
docker run --rm \
  -v "$root/platform/prometheus:/work" \
  -w /work \
  --entrypoint promtool \
  prom/prometheus:v3.4.0 \
  test rules alerts_test.yml

docker run --rm \
  -v "$root/platform/loki/loki.yml:/etc/loki/loki.yml:ro" \
  grafana/loki:3.4.2 \
  -verify-config -config.file=/etc/loki/loki.yml

docker run --rm \
  -v "$root/platform/alloy/config.alloy:/etc/alloy/config.alloy:ro" \
  grafana/alloy:v1.8.1 \
  fmt --test /etc/alloy/config.alloy

am=$(mktemp -d)
sed 's/__TELEGRAM_CHAT_ID__/1/' platform/alertmanager/alertmanager.yml.tmpl > "$am/alertmanager.yml"
cp platform/alertmanager/telegram.tmpl "$am/telegram.tmpl"
printf '0\n' > "$am/telegram_token"
docker run --rm \
  -v "$am/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
  -v "$am/telegram.tmpl:/etc/alertmanager/telegram.tmpl:ro" \
  -v "$am/telegram_token:/etc/alertmanager/telegram_token:ro" \
  --entrypoint amtool \
  prom/alertmanager:v0.28.0 \
  check-config /etc/alertmanager/alertmanager.yml

docker run --rm \
  -v "$root:/work" \
  -w /work \
  python:3.12-alpine \
  sh -c 'pip install -q yamllint==1.35.1 && yamllint -c .yamllint.yml compose.yml runner/compose.yml platform .github'

docker run --rm \
  -v "$root:/work" \
  -w /work \
  koalaman/shellcheck:v0.10.0 \
  scripts/ci.sh scripts/deploy.sh

docker run --rm \
  -v "$root:/repo" \
  -w /repo \
  rhysd/actionlint:1.7.7 \
  -color

docker run --rm \
  -v "$root:/repo" \
  ghcr.io/gitleaks/gitleaks:v8.24.2 \
  detect --no-git --source /repo --redact

echo "ci ok"
