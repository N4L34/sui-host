"""Disposable real Compose wiring test, no host policy or production network.

The three real role containers use distinct private directory mounts, static
addresses, real Docker bridges and source admission. A synthetic TLS fixture
on the private test uplink never dials a request destination.
"""

import contextlib
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from egress.bundles import seal
from egress.compiler import compile_installation
from egress.private_files import write_private_json
from egress.probes import Probe, probe
from egress.tests.test_runtime import installation
from lab import certificate, origin, proxy
from runtime_lab import doh


def fixture():
    with tempfile.TemporaryDirectory() as d:
        ca, tls = certificate(d)
        # The operator-created CA is used by every role; reuse its fixture
        # key/cert rather than emitting private material to container output.
        import ssl

        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain("/fixture/cert.pem", "/fixture/key.pem")
        with origin(tls) as (target, _), doh(tls) as (resolver, _):
            # Fixed loopback map only; arbitrary destination dialing is absent.
            with proxy(
                {b"8.8.4.4:443": target, b"1.1.1.1:443": resolver},
                "http",
                tls,
                ("test", "fixture-password"),
                bind="0.0.0.0",
            ) as (address, _):
                Path("/tmp/fixture-port").write_text(str(address[1]))
                while True:
                    time.sleep(1)


def client():
    expected = os.environ.get("LAB_EXPECTED", "ok")
    for protocol, port in [("http", 3128), ("socks5", 1080)]:
        result = probe(
            Probe(
                "lab",
                "effective",
                protocol,
                os.environ["LAB_GATEWAY"],
                port,
                "origin.test.invalid",
                443,
                "/fixture/cert.pem",
            ),
            timeout=5,
        )
        if (result.reason == "ok") != (expected == "ok"):
            raise ValueError("compose_client_failed")
    if expected == "ok":
        import socket

        # Candidate staging listeners are never exposed on ingress interfaces.
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex((os.environ["LAB_GATEWAY"], 33128)) == 0:
                raise ValueError("stage_listener_exposed")
    print("compose_client_ok")


def run_compose():
    root = Path(__file__).resolve().parents[2]
    project = "egress-lab-" + uuid.uuid4().hex[:10]
    prefix = project.replace("-", "_")
    with tempfile.TemporaryDirectory(prefix="sui-egress-compose-") as directory:
        temporary = Path(directory).resolve()
        (temporary / "fixture").mkdir(mode=0o700)
        source_root = root
        root = temporary / "workspace"
        for folder in ("egress", "egress/tests"):
            for source in (source_root / folder).glob("*.py"):
                target = root / folder / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        for name in ("egress/compose.yml", "egress/examples/registry.example.json"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_root / name, target)
        cert, tls = certificate(temporary / "fixture")
        config = installation()
        config.update(uid=os.getuid(), gid=os.getgid())
        config["ingress"][0].update(
            subnet="172.30.210.0/24",
            gateway="172.30.210.2",
            dns="172.30.210.3",
            status="172.30.210.4",
            sources=["172.30.210.10"],
        )
        config["control"].update(
            subnet="172.30.211.0/24",
            gateway="172.30.211.2",
            dns="172.30.211.3",
            status="172.30.211.4",
        )
        config["uplink"].update(subnet="172.30.212.0/24", gateway="172.30.212.2")
        config["monitoring"].update(
            gateway="172.30.213.2", dns="172.30.213.3", status="172.30.213.4"
        )
        config["registry"]["profiles"][0]["routes"][0]["matcher"]["value"] = (
            "origin.test.invalid"
        )
        config["probe"].update(
            target="origin.test.invalid", ca_pem=Path(cert).read_text()
        )
        config["resolver"].update(
            server_name="resolver.test.invalid", ca_pem=Path(cert).read_text()
        )
        config["upstreams"][0].update(
            server_name="upstream.test.invalid",
            password="fixture-password",
            ca_pem=Path(cert).read_text(),
        )
        bundle = compile_installation(config)
        topology = bundle["compose"]
        for name, n in topology["networks"].items():
            n["name"] = prefix + "_" + name
        topology["networks"]["monitoring"] = {
            "external": True,
            "name": prefix + "_monitoring",
        }
        # Explicit static MACs exercise actual Compose/interface wiring even
        # for this pilot, without installing any host enforcement.
        count = 0
        for service in topology["services"].values():
            for network in service["networks"].values():
                count += 1
                network["mac_address"] = f"02:00:00:01:00:{count:02x}"
        common = {
            "image": "sui-egress-test:2026.11.12",
            "user": f"{os.getuid()}:{os.getgid()}",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "volumes": [
                {
                    "type": "bind",
                    "source": str(root),
                    "target": "/workspace",
                    "read_only": True,
                },
                {
                    "type": "bind",
                    "source": str(temporary / "fixture"),
                    "target": "/fixture",
                    "read_only": True,
                },
            ],
            "entrypoint": ["python3", "/workspace/egress/tests/compose_lab.py"],
        }
        topology["services"]["fixture"] = dict(
            common,
            command=["fixture"],
            networks={"uplink": {"ipv4_address": "172.30.212.10"}},
        )
        topology["services"]["client"] = dict(
            common,
            command=["client"],
            profiles=["test-client"],
            environment={"LAB_GATEWAY": "172.30.210.2"},
            networks={"ingress-demo-pilot": {"ipv4_address": "172.30.210.10"}},
        )
        topology["services"]["foreign"] = dict(
            common,
            command=["client"],
            profiles=["test-client"],
            environment={"LAB_GATEWAY": "172.30.210.2", "LAB_EXPECTED": "deny"},
            networks={"ingress-demo-pilot": {"ipv4_address": "172.30.210.11"}},
        )
        override = temporary / "compose.json"
        override.write_text(json.dumps(topology))
        override.chmod(0o600)
        args = [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--project-directory",
            str(temporary),
            "-p",
            project,
            "-f",
            str(root / "egress/compose.yml"),
            "-f",
            str(override),
        ]

        def command(*cmd, check=True, timeout=90):
            result = subprocess.run(
                list(cmd), capture_output=True, text=True, timeout=timeout
            )
            if check and result.returncode:
                print(
                    "compose_operation_failed:"
                    + (
                        "fixture"
                        if "fixture" in cmd
                        else "role"
                        if "gateway" in cmd
                        else "other"
                    ),
                    file=sys.stderr,
                )
                print(
                    result.stderr[:3000].replace("fixture-password", "redacted"),
                    file=sys.stderr,
                )
                raise ValueError("compose_operation_failed")
            return result.stdout

        created = False
        try:
            command(
                "docker",
                "network",
                "create",
                "--subnet",
                "172.30.213.0/24",
                prefix + "_monitoring",
            )
            created = True
            command(*args, "up", "--no-build", "-d", "fixture")
            fixture_port = None
            for _ in range(30):
                try:
                    fixture_port = int(
                        command(
                            *args,
                            "exec",
                            "-T",
                            "fixture",
                            "cat",
                            "/tmp/fixture-port",
                            check=False,
                        ).strip()
                    )
                    break
                except ValueError:
                    time.sleep(0.2)
            if fixture_port is None:
                raise ValueError("fixture_start_failed")
            bundle["gateway"]["singbox"]["outbounds"][1].update(
                server="172.30.212.10", server_port=fixture_port
            )
            for role in ("gateway", "dns", "status"):
                write_private_json(
                    temporary / "runtime" / role / "runtime.json", seal(bundle[role])
                )
            command(*args, "up", "--no-build", "-d", "gateway", "dns", "status")
            command(
                *args,
                "exec",
                "-T",
                "status",
                "python3",
                "-m",
                "egress.smoke",
                timeout=70,
            )
            command(*args, "run", "--rm", "--no-deps", "client")
            command(*args, "run", "--rm", "--no-deps", "foreign")
            # Native private auth never reaches Docker stdout/stderr.
            logs = command(*args, "logs", "--no-color", "gateway", "dns", "status")
            if any(
                x in logs
                for x in (
                    "fixture-password",
                    "Proxy-Authorization",
                    "upstream.test.invalid",
                )
            ):
                raise ValueError("compose_log_canary")
            print("egress_compose_smoke_ok")
        finally:
            command(*args, "stop", check=False)
            command(*args, "rm", "--force", check=False)
            for name in list(topology["networks"]):
                if name != "monitoring":
                    command("docker", "network", "rm", prefix + "_" + name, check=False)
            if created:
                command("docker", "network", "rm", prefix + "_monitoring", check=False)


if __name__ == "__main__":
    try:
        {"fixture": fixture, "client": client, "run": run_compose}[sys.argv[1]]()
    except Exception:
        print("compose_lab_failed", file=sys.stderr)
        raise SystemExit(1)
