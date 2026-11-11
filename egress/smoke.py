"""Bounded post-up checks from the status container; safe fixed diagnostics."""

import concurrent.futures
import http.client
import json
from pathlib import Path
import sys
import time

from .bundles import validate_bundle
from .daemon import _probe_config, dns_probe
from .private_files import load_private_json
from .probes import probe


def required_ready(probes, successes):
    required = [i for i, p in enumerate(probes) if p["required"]]
    return bool(required) and all(successes[i] >= 2 for i in required)


def main():
    deadline = time.monotonic() + 60
    try:
        config = load_private_json(
            "/run/private/runtime.json", lambda v: validate_bundle(v, "status")
        )
        successes = {i: 0 for i in range(len(config["probes"]))}
        ca = Path("/tmp/egress/probe-ca.pem")
        while time.monotonic() < deadline:

            def check(pair):
                i, p = pair
                return i, probe(
                    _probe_config(p, ca),
                    timeout=min(5, max(0.1, deadline - time.monotonic())),
                ).reason == "ok"

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                for i, ok in pool.map(check, enumerate(config["probes"])):
                    successes[i] = successes[i] + 1 if ok else 0
            dns_ok = dns_probe(
                config["dns_ip"],
                config["dns_port"],
                config["dns_target"],
                timeout=min(3, max(0.1, deadline - time.monotonic())),
            )
            if required_ready(config["probes"], successes) and dns_ok:
                conn = http.client.HTTPConnection(
                    config["listen"], config["port"], timeout=1
                )
                try:
                    conn.request("GET", "/health/ready")
                    response = conn.getresponse()
                    response.read(65536)
                    if response.status == 200:
                        print("egress_smoke_ok")
                        return 0
                finally:
                    conn.close()
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    except Exception:
        pass
    print("egress_smoke_failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
