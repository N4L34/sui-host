#!/usr/bin/env python3
"""Write Prometheus file_sd from Docker labels and platform/services.json.

Labels on a container that sits on the docker network `monitoring`:

  monitoring.service        stable name
  monitoring.dns            name Prometheus uses on that network
  monitoring.probe          comma-separated HTTP URLs
  monitoring.metrics.port   port to scrape
  monitoring.metrics.path   default /metrics
  monitoring.alert          page, if a failed probe should wake Telegram
  monitoring.name           short name for friends
  monitoring.summary        firing text
  monitoring.resolved       resolved text

platform/services.json lists containers created before those labels existed.
A label on the container wins. A page-worthy record stays for six hours after
the container disappears, so a removed service still fails its probe.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "platform" / "services.json"
STATE_PATH = ROOT / "runtime" / "discover-state.json"
OUT_DIR = ROOT / "runtime" / "file_sd"
REMEMBER_S = 6 * 3600
NETWORK = "monitoring"


def split_probes(value: str | None) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in str(value).split(","):
        url = part.strip()
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def load_inventory() -> dict[str, dict]:
    if not INVENTORY.is_file():
        return {}
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    found: dict[str, dict] = {}
    for item in data.get("services", []):
        match = item.get("match")
        if match:
            found[str(match)] = item
    return found


def inspect_containers() -> list[dict]:
    ids = subprocess.check_output(["docker", "ps", "-aq"], text=True).split()
    if not ids:
        return []
    raw = subprocess.check_output(["docker", "inspect", *ids], text=True)
    return json.loads(raw)


def _looks_like_id(value: str) -> bool:
    return len(value) >= 12 and all(c in "0123456789abcdef" for c in value)


def dns_name(container: dict, labels: dict, inv: dict, service: str) -> str:
    if labels.get("monitoring.dns"):
        return str(labels["monitoring.dns"])
    if inv.get("dns"):
        return str(inv["dns"])
    nets = (container.get("NetworkSettings") or {}).get("Networks") or {}
    aliases = (nets.get(NETWORK) or {}).get("Aliases") or []
    name = container.get("Name", "").lstrip("/")
    for alias in aliases:
        if alias and alias != name and not _looks_like_id(alias):
            return alias
    return service or name


def record_from(container: dict, inv_by_name: dict) -> dict | None:
    name = container.get("Name", "").lstrip("/")
    labels = (container.get("Config") or {}).get("Labels") or {}
    inv = inv_by_name.get(name, {})
    has_label = any(str(key).startswith("monitoring.") for key in labels)
    if not has_label and not inv:
        return None
    service = str(labels.get("monitoring.service") or inv.get("service") or "")
    if not service:
        return None
    probes = split_probes(labels.get("monitoring.probe"))
    if not probes:
        probes = split_probes(",".join(inv.get("probes") or []))
    if "monitoring.alert" in labels:
        alert = str(labels.get("monitoring.alert") or "")
    else:
        alert = str(inv.get("alert") or "")
    metrics_port = labels.get("monitoring.metrics.port") or inv.get("metrics_port") or ""
    metrics_path = str(labels.get("monitoring.metrics.path") or inv.get("metrics_path") or "/metrics")
    nets = (container.get("NetworkSettings") or {}).get("Networks") or {}
    ip = str(((nets.get(NETWORK) or {}).get("IPAddress")) or "")
    running = bool((container.get("State") or {}).get("Running"))
    return {
        "service": service,
        "dns": dns_name(container, labels, inv, service),
        "probes": probes,
        "alert": alert,
        "name": str(labels.get("monitoring.name") or inv.get("name") or ""),
        "summary": str(labels.get("monitoring.summary") or inv.get("summary") or ""),
        "resolved": str(labels.get("monitoring.resolved") or inv.get("resolved") or ""),
        "metrics_port": str(metrics_port) if metrics_port else "",
        "metrics_path": metrics_path or "/metrics",
        "running": running,
        "ip": ip,
    }


def prefer(current: dict | None, candidate: dict) -> dict:
    if current is None:
        return candidate
    current_live = current["running"] and current["ip"]
    candidate_live = candidate["running"] and candidate["ip"]
    if candidate_live and not current_live:
        return candidate
    if candidate["running"] and not current["running"]:
        return candidate
    return current


def remembered(state: dict, now: float) -> dict[str, dict]:
    kept: dict[str, dict] = {}
    for service, rec in state.items():
        if rec.get("alert") != "page":
            continue
        seen = float(rec.get("seen") or 0)
        if now - seen <= REMEMBER_S and rec.get("probes"):
            kept[str(service)] = rec
    return kept


def build(records: dict[str, dict], state: dict, now: float) -> tuple[list, list, dict]:
    new_state: dict[str, dict] = {}
    for service, rec in records.items():
        stored = {key: value for key, value in rec.items() if key not in {"running", "ip"}}
        stored["seen"] = now
        new_state[service] = stored
    for service, rec in remembered(state, now).items():
        if service not in new_state:
            new_state[service] = rec
    probes: list[dict] = []
    metrics: list[dict] = []
    for service, rec in sorted(new_state.items()):
        labels: dict[str, str] = {"service": service}
        if rec.get("alert"):
            labels["monitoring_alert"] = rec["alert"]
        if rec.get("name"):
            labels["monitoring_name"] = rec["name"]
        if rec.get("summary"):
            labels["monitoring_summary"] = rec["summary"]
        if rec.get("resolved"):
            labels["monitoring_resolved"] = rec["resolved"]
        for url in rec.get("probes") or []:
            probes.append({"targets": [url], "labels": dict(labels)})
        live = records.get(service)
        if live and live.get("running") and live.get("ip") and live.get("metrics_port"):
            metrics.append(
                {
                    "targets": [f"{live['dns']}:{live['metrics_port']}"],
                    "labels": {
                        "service": service,
                        "metrics_path": live.get("metrics_path") or "/metrics",
                    },
                }
            )
    return probes, metrics, new_state


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o644)
    temporary.replace(path)


def main() -> None:
    if "--self-test" in sys.argv:
        self_test()
        return
    inventory = load_inventory()
    found: list[dict] = []
    for container in inspect_containers():
        rec = record_from(container, inventory)
        if rec:
            found.append(rec)
    records: dict[str, dict] = {}
    for rec in found:
        records[rec["service"]] = prefer(records.get(rec["service"]), rec)
    state: dict = {}
    if STATE_PATH.is_file():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    now = time.time()
    probes, metrics, new_state = build(records, state, now)
    write_json(OUT_DIR / "probes.json", probes)
    write_json(OUT_DIR / "metrics.json", metrics)
    write_json(STATE_PATH, new_state)


def self_test() -> None:
    now = 1_000_000.0
    inventory = {
        "chat-synapse-1": {
            "service": "synapse",
            "dns": "synapse",
            "probes": [
                "http://synapse:8008/health",
                "http://synapse:8008/health",
                "http://synapse:8008/_matrix/client/versions",
            ],
            "alert": "page",
            "name": "Чат",
            "summary": "Чат не отвечает.",
            "resolved": "Чат снова отвечает.",
            "metrics_port": "9090",
            "metrics_path": "/_synapse/metrics",
        }
    }
    synapse = {
        "Name": "/chat-synapse-1",
        "Config": {"Labels": {}},
        "State": {"Running": True},
        "NetworkSettings": {
            "Networks": {NETWORK: {"IPAddress": "172.30.0.2", "Aliases": ["synapse"]}}
        },
    }
    rec = record_from(synapse, inventory)
    assert rec is not None
    assert rec["probes"] == [
        "http://synapse:8008/health",
        "http://synapse:8008/_matrix/client/versions",
    ]
    grafana = {
        "Name": "/monitoring-grafana-1",
        "Config": {
            "Labels": {
                "monitoring.service": "grafana",
                "monitoring.dns": "grafana",
                "monitoring.probe": "http://grafana:3000/api/health",
                "monitoring.alert": "page",
                "monitoring.name": "Страницы",
                "monitoring.summary": "Страницы мониторинга не открываются.",
                "monitoring.resolved": "Страницы мониторинга снова открываются.",
            }
        },
        "State": {"Running": True},
        "NetworkSettings": {"Networks": {NETWORK: {"IPAddress": "172.30.0.3", "Aliases": []}}},
    }
    demo = {
        "Name": "/demo-1",
        "Config": {
            "Labels": {
                "monitoring.service": "demo",
                "monitoring.probe": "http://demo:80/",
                "monitoring.alert": "page",
                "monitoring.metrics.port": "9100",
            }
        },
        "State": {"Running": False},
        "NetworkSettings": {"Networks": {}},
    }
    records: dict[str, dict] = {}
    for item in (rec, record_from(grafana, {}), record_from(demo, {})):
        assert item is not None
        records[item["service"]] = prefer(records.get(item["service"]), item)
    probes, metrics, state = build(records, {}, now)
    assert any(item["targets"] == ["http://synapse:8008/health"] for item in probes)
    assert any(
        item["targets"] == ["synapse:9090"] and item["labels"]["metrics_path"] == "/_synapse/metrics"
        for item in metrics
    )
    assert all(item["labels"]["service"] != "demo" for item in metrics)
    probes_later, metrics_later, _ = build({}, state, now + 100)
    assert any(item["labels"]["service"] == "synapse" for item in probes_later)
    assert metrics_later == []
    probes_gone, _, _ = build({}, state, now + REMEMBER_S + 1)
    assert probes_gone == []
    labeled = {
        "Name": "/chat-synapse-1",
        "Config": {
            "Labels": {
                "monitoring.service": "synapse",
                "monitoring.probe": "http://synapse:8008/health",
                "monitoring.alert": "page",
            }
        },
        "State": {"Running": True},
        "NetworkSettings": {"Networks": {NETWORK: {"IPAddress": "172.30.0.2"}}},
    }
    both = record_from(labeled, inventory)
    assert both is not None
    assert both["probes"] == ["http://synapse:8008/health"]
    plain = {"Name": "/plain-1", "Config": {"Labels": {}}, "State": {"Running": True}, "NetworkSettings": {}}
    assert record_from(plain, {}) is None
    print("discover self-test ok")


if __name__ == "__main__":
    main()
