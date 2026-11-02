#!/usr/bin/env python3
"""Discover deployable Compose projects from repository conventions."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent


def _safe_relative(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not value:
        raise ValueError(f"invalid project-relative path: {value!r}")
    return path.as_posix()


def discover_services(root: Path = ROOT) -> list[dict[str, object]]:
    services: dict[str, dict[str, object]] = {}
    systemd = root / "systemd"
    for project in sorted(path for path in root.iterdir() if path.is_dir()):
        compose = project / "compose.yml"
        unit = systemd / f"{project.name}.service"
        if compose.is_file() and unit.is_file():
            services[project.name] = {
                "name": project.name,
                "path": project.name,
                "compose_files": ["compose.yml"],
                "deploy_hook": "",
            }

    for descriptor in sorted(root.glob("*/deploy/project.json")):
        project = descriptor.parents[1]
        config = json.loads(descriptor.read_text(encoding="utf-8"))
        compose_files = config.get("compose_files")
        if not isinstance(compose_files, list) or not compose_files:
            raise ValueError(f"{descriptor}: compose_files must be a non-empty list")
        for item in compose_files:
            _safe_relative(str(item))
        hook = _safe_relative(str(config.get("deploy_hook", ""))) if config.get("deploy_hook") else ""
        services[project.name] = {
            "name": project.name,
            "path": project.name,
            "compose_files": [str(item) for item in compose_files],
            "deploy_hook": hook,
        }
    return [services[name] for name in sorted(services)]


def changed_services(root: Path, paths: Iterable[str]) -> list[dict[str, object]]:
    changed_roots = {Path(path).parts[0] for path in paths if Path(path).parts}
    return [service for service in discover_services(root) if service["path"] in changed_roots]


def main() -> int:
    services = discover_services()
    if len(sys.argv) > 1 and sys.argv[1] == "--changed":
        paths = (line.rstrip("\n") for line in sys.stdin)
        selected = changed_services(ROOT, paths)
        for service in selected:
            print(service["name"])
    elif len(sys.argv) > 1 and sys.argv[1] == "--compose-projects":
        for service in services:
            if service["deploy_hook"]:
                continue
            print(f"{service['path']}\t{service['compose_files'][0]}")
    elif len(sys.argv) > 2 and sys.argv[1] == "--compose-files":
        selected = next((item for item in services if item["name"] == sys.argv[2]), None)
        if selected is None:
            raise SystemExit(f"unknown service: {sys.argv[2]}")
        for compose_file in selected["compose_files"]:
            print(compose_file)
    elif len(sys.argv) > 2 and sys.argv[1] == "--deploy-hook":
        selected = next((item for item in services if item["name"] == sys.argv[2]), None)
        if selected is None:
            raise SystemExit(f"unknown service: {sys.argv[2]}")
        print(selected["deploy_hook"])
    elif len(sys.argv) > 1 and sys.argv[1] == "--json":
        print(json.dumps(services, ensure_ascii=False, indent=2))
    else:
        print("usage: service_catalog.py [--changed|--compose-projects|--compose-files NAME|--deploy-hook NAME|--json]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
