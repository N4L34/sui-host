#!/bin/bash
# Synthetic local/container checks only. No node, private env, or deployments.
set -euo pipefail
cd "$(dirname "$0")/.."
root=$PWD
python3 -m unittest discover -s egress/tests -p 'test_*.py' -v

temporary=$(mktemp -d)
trap 'python3 -c "import shutil,sys; shutil.rmtree(sys.argv[1])" "$temporary"' EXIT
python3 - "$temporary" <<'PY'
import json
import os
from pathlib import Path
import shutil
import sys
from egress.install import render
from egress.private_files import write_private_json
from egress.tests.test_runtime import installation
from scripts.service_catalog import discover_services
root=Path(sys.argv[1]).resolve();config=installation();config.update(uid=os.getuid(),gid=os.getgid())
write_private_json(root/'input'/'installation.json',config)
render(root/'input'/'installation.json',root/'project')
service=next(s for s in discover_services() if s['name']=='egress')
assert service['deploy_hook']=='deploy.sh'
# Copy only explicitly selected public source/fixtures. Never mount a checkout
# containing operator runtime, .env, Xray configuration or credentials.
fixture=root/'fixture'
for directory in ('egress','egress/tests'):
    for source in Path(directory).glob('*.py'):
        destination=fixture/source;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
for name in ('egress/compose.yml','egress/examples/registry.example.json','commands/app/scripts/egress_client.py','avtobot/egress_client.py'):
    destination=fixture/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(name,destination)
print('egress private render and service catalog ok')
PY

docker compose --env-file /dev/null --project-directory "$temporary/project" \
  -f "$root/egress/compose.yml" -f "$temporary/project/compose.override.yml" \
  config --format json > "$temporary/rendered.json"
python3 scripts/policy.py < "$temporary/rendered.json"
python3 - "$temporary/rendered.json" <<'PY'
import json,sys
config=json.load(open(sys.argv[1]));assert config['name']=='egress'
for name,service in config['services'].items():
    assert not service.get('ports') and service['cap_drop']==['ALL']
    assert service['read_only'] and service.get('network_mode')!='host'
    assert 'chat_data' not in service['networks']
    assert 'monitoring' in service['networks']
    mounts=[v for v in service['volumes'] if v['target']=='/run/private']
    assert len(mounts)==1 and mounts[0]['source'].endswith('/runtime/'+name)
print('egress hardened topology ok')
PY

docker build --target test -t sui-egress-test:2026.11.12 egress
container=(docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges:true
  --mount "type=bind,src=$temporary/fixture,dst=/workspace,readonly" -w /workspace
  --entrypoint python3 sui-egress-test:2026.11.12)
"${container[@]}" egress/tests/singbox_lab.py --binary /usr/local/bin/sing-box --gost /usr/local/bin/gost
"${container[@]}" egress/tests/deadline_lab.py
"${container[@]}" egress/tests/runtime_lab.py
# The only added capability belongs to this disposable, isolated test namespace.
docker run --rm --network none --user 0:0 --cap-drop ALL --cap-add NET_ADMIN \
  --sysctl net.ipv6.conf.all.disable_ipv6=1 --sysctl net.ipv6.conf.default.disable_ipv6=1 \
  --security-opt no-new-privileges:true \
  --mount "type=bind,src=$temporary/fixture,dst=/workspace,readonly" -w /workspace \
  --entrypoint python3 sui-egress-test:2026.11.12 egress/tests/kernel_lab.py

# Exercise actual separate Compose roles, static source admission and mounts.
docker build --target runtime -t sui-egress:2026.11.12 egress
python3 "$temporary/fixture/egress/tests/compose_lab.py" run
