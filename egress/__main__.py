"""Offline registry validation or one bounded probe; no deployment actions."""

import argparse
from dataclasses import fields
import json
import sys

from .policy import validate_registry
from .private_files import load_private_json
from .probes import Probe, probe


def probe_config(value):
    allowed = {field.name for field in fields(Probe)}
    required = {"profile", "upstream", "protocol", "proxy_ip", "proxy_port", "target"}
    if (
        type(value) is not dict
        or not required <= value.keys()
        or not value.keys() <= allowed
    ):
        raise ValueError("probe_config")
    return Probe(**value)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid_arguments\n")


def main():
    parser = SafeParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "probe"])
    parser.add_argument(
        "file", help="private JSON file (owned 0600 in an owned 0700 directory)"
    )
    args = parser.parse_args()
    try:
        if args.command == "validate":
            load_private_json(args.file, validate_registry)
            print("registry_valid_pilot")
            return 0
        config = load_private_json(args.file, probe_config)
        result = probe(config)
        # Config errors may contain invalid IDs, so omit all caller identifiers.
        output = {
            "reason": result.reason,
            "duration_seconds": round(result.duration, 3),
        }
        if result.reason != "config":
            output.update(
                profile=config.profile,
                upstream=config.upstream,
                protocol=config.protocol,
            )
        print(json.dumps(output))
        return 0 if result.reason == "ok" else 1
    except Exception:
        print("validation_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
