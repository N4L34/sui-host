"""Render private installation files; no Docker or host network mutation."""

import argparse
import json
import os
from pathlib import Path
import sys

from .compiler import compile_installation
from .__main__ import SafeParser
from .private_files import load_private_json, write_private_json


def render(source, destination):
    value = load_private_json(source)
    bundle = compile_installation(value)
    if value["uid"] != os.getuid() or value["gid"] != os.getgid():
        raise ValueError("installation_owner")
    root = Path(destination).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (root / "runtime").mkdir(mode=0o700, exist_ok=True)
    # Validate/serialize the entire bundle before changing any active file.
    json.dumps(bundle, allow_nan=False)
    for component in ("gateway", "dns", "status"):
        directory = root / "runtime" / component
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private_json(directory / "runtime.json", bundle[component])
    # Compose YAML accepts JSON. Only IPAM/topology and numeric UID reach this
    # file, no destinations/auth. Private anyway, because installation IPAM.
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private_json(root / "runtime" / "topology.json", bundle["compose"])
    # Root project directory need not be 0700: it is a checkout. Publish the
    # non-secret override by replace from its private runtime staging folder.
    os.replace(root / "runtime" / "topology.json", root / "compose.override.yml")
    if "enforcement" in value:
        write_private_json(
            root / "runtime" / "enforcement" / "inventory.json", value["enforcement"]
        )
    return bundle


def main():
    p = SafeParser(description=__doc__)
    p.add_argument("source")
    p.add_argument("--destination", default="egress")
    a = p.parse_args()
    try:
        render(a.source, a.destination)
        print("installation_rendered")
        return 0
    except Exception:
        print("installation_invalid", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
