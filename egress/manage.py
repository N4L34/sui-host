"""Explicit subscription apply and client Compose overrides; no host routing."""

import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
from .service import private_write, subscription_config

ROOT = Path(__file__).resolve().parents[1]
MARKER = "sui-egress-simple-v1"


def client_override(key, exclude):
    project, service = key.split("/")
    if not all(re.fullmatch("[a-z][a-z0-9_-]*", x) for x in (project, service)):
        raise ValueError("client_invalid")
    env = {
        "HTTP_PROXY": "http://sui-egress:3128",
        "HTTPS_PROXY": "http://sui-egress:3128",
        "ALL_PROXY": "socks5h://sui-egress:1080",
        "NO_PROXY": ",".join(["localhost", "127.0.0.1", "::1", service, *exclude]),
    }
    env.update({k.lower(): v for k, v in list(env.items())})
    if project in ("commands", "avtobot"):
        env.update(
            SUI_EGRESS_MODE="required",
            SUI_EGRESS_HOST="sui-egress",
            SUI_EGRESS_PORT="1080",
        )
    return {"x-egress-managed": MARKER, "services": {service: {"environment": env}}}


def run(args, project="egress", timeout=90):
    r = subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT / project,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode:
        raise RuntimeError("compose_failed")
    return r.stdout


def enroll():
    desired = json.loads((ROOT / "egress/clients.json").read_text())["services"]
    if not isinstance(desired, list) or len(set(desired)) != len(desired):
        raise ValueError("clients_invalid")
    groups = {}
    for key in desired:
        override = client_override(
            key, ["synapse"] if key == "commands/commands" else []
        )
        project, _ = key.split("/")
        base = json.loads(
            run(["-f", "compose.yml", "config", "--format", "json"], project)
        )
        service = key.split("/")[1]
        if service not in base["services"] or "monitoring" not in base["services"][
            service
        ].get("networks", {}):
            raise ValueError("client_network_invalid")
        groups.setdefault(project, {"x-egress-managed": MARKER, "services": {}})[
            "services"
        ].update(override["services"])
    previous = ROOT / "egress/runtime/clients.json"
    old = json.loads(previous.read_text()) if previous.exists() else []
    for project in sorted(set(old) | set(groups)):
        path = ROOT / project / "compose.override.yml"
        if (
            path.exists()
            and json.loads(path.read_text()).get("x-egress-managed") != MARKER
        ):
            raise ValueError("existing_override_requires_merge")
        old_override = json.loads(path.read_text()) if path.exists() else None
        new = groups.get(project)
        if new and path.exists() and json.loads(path.read_text()) == new:
            continue
        if new:
            # Overrides contain no upstream credentials; atomic publication.
            temp = ROOT / "egress/runtime" / f"{project}-override.json"
            private_write(temp, new)
            os.replace(temp, path)
        elif path.exists():
            path.unlink()
        services = sorted(
            set((new or {"services": {}})["services"])
            | set((old_override or {"services": {}})["services"])
        )
        try:
            run(
                ["up", "--no-deps", "-d", "--wait", "--wait-timeout", "60", *services],
                project,
            )
        except Exception:
            if old_override:
                temp = ROOT / "egress/runtime" / f"{project}-rollback.json"
                private_write(temp, old_override)
                os.replace(temp, path)
            elif path.exists():
                path.unlink()
            run(["up", "--no-deps", "-d", *services], project)
            raise RuntimeError("client_rolled_back") from None
        if project not in old and new:
            old.append(project)
        if project in old and not new:
            old.remove(project)
        private_write(previous, old)
        print("client_applied " + project, flush=True)
    private_write(previous, list(groups))


def apply():
    urlfile = ROOT / "egress/secrets/subscription.url"
    if urlfile.is_symlink() or urlfile.stat().st_mode & 0o077:
        raise ValueError("subscription_permissions")
    url = urlfile.read_text().strip()
    if not url.startswith("https://"):
        raise ValueError("subscription_url_invalid")
    request = urllib.request.Request(url, headers={"User-Agent": "v2rayNG/1.9"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("subscription_too_large")
    value = subscription_config(raw)
    candidate = ROOT / "egress/runtime/candidate.json"
    private_write(candidate, value)
    if "running" not in run(["ps", "--format", "json"]):
        raise ValueError("start_gateway_before_apply")
    run(
        [
            "exec",
            "-T",
            "proxy",
            "python3",
            "-m",
            "egress.service",
            "check",
            "/run/private/candidate.json",
        ]
    )
    private_write(ROOT / "egress/runtime/config.json", value)
    # The supervisor validates and restarts only Xray in the existing container.
    import time

    expected = hashlib.sha256(
        (ROOT / "egress/runtime/config.json").read_bytes()
    ).hexdigest()
    for _ in range(45):
        time.sleep(1)
        try:
            applied = json.loads((ROOT / "egress/runtime/applied.json").read_text())
            if applied != expected:
                continue
            run(
                [
                    "exec",
                    "-T",
                    "proxy",
                    "python3",
                    "-c",
                    "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2)",
                ]
            )
            break
        except Exception:
            pass
    else:
        raise ValueError("gateway_not_ready")
    enroll()
    print("egress_applied")


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["apply"]:
            raise ValueError("usage: scripts/egress apply")
        apply()
    except Exception:
        print(
            "egress_apply_failed; previous working configuration retained where possible",
            file=sys.stderr,
        )
        sys.exit(1)
