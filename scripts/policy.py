#!/usr/bin/env python3
"""Check a rendered Compose config against the node contract.

Reads `docker compose config --format json` from stdin.
"""

from __future__ import annotations

import json
import sys

PRIVILEGED = {"cadvisor"}
DOCKER_SOCK = {"alloy"}
BUDGET_MIB = 8 * 1024


def mib(value: object) -> float:
    if isinstance(value, int):
        return value / (1024 * 1024)
    text = str(value).strip()
    if text.isdigit():
        return int(text) / (1024 * 1024)
    number = float(text[:-1])
    unit = text[-1].upper()
    scale = {"K": 1 / 1024, "M": 1, "G": 1024}
    if unit not in scale:
        raise ValueError(f"непонятный размер {value}")
    return number * scale[unit]


def main() -> int:
    spec = json.load(sys.stdin)
    services = spec.get("services") or {}
    errors: list[str] = []
    total = 0.0
    for name, service in services.items():
        image = str(service.get("image") or "")
        if ":" not in image or image.endswith(":latest"):
            errors.append(f"{name}: образ без закреплённого тега ({image or 'пусто'})")
        limit = ((service.get("deploy") or {}).get("resources") or {}).get("limits") or {}
        memory = limit.get("memory")
        if not memory:
            errors.append(f"{name}: нет предела памяти")
        else:
            total += mib(str(memory))
            swap = service.get("memswap_limit")
            if not swap:
                errors.append(f"{name}: нет memswap_limit")
            elif abs(mib(str(swap)) - mib(str(memory))) > 0.1:
                errors.append(f"{name}: memswap_limit {swap} не равен памяти {memory}")
        logging = service.get("logging") or {}
        options = logging.get("options") or {}
        if logging.get("driver") != "json-file" or options.get("max-size") != "10m" or str(options.get("max-file")) != "5":
            errors.append(f"{name}: журнал не json-file 10m x 5")
        if service.get("restart") != "unless-stopped":
            errors.append(f"{name}: restart не unless-stopped")
        opts = service.get("security_opt") or []
        if "no-new-privileges:true" not in opts:
            errors.append(f"{name}: нет no-new-privileges")
        if bool(service.get("privileged")) != (name in PRIVILEGED):
            errors.append(f"{name}: privileged только у cAdvisor")
        for volume in service.get("volumes") or []:
            source = volume.get("source") if isinstance(volume, dict) else ""
            if source in {"/var/run/docker.sock", "/run/docker.sock"} and name not in DOCKER_SOCK:
                errors.append(f"{name}: сокет Docker только у Alloy")
        for port in service.get("ports") or []:
            if isinstance(port, str):
                host = port.split(":")[0]
            else:
                host = str(port.get("host_ip") or "")
            if host not in {"127.0.0.1", "::1"}:
                errors.append(f"{name}: порт опубликован не на 127.0.0.1 ({host or port})")
    if total > BUDGET_MIB:
        errors.append(f"сумма лимитов {total:.0f} МиБ выше бюджета {BUDGET_MIB} МиБ")
    if errors:
        for line in errors:
            print(line, file=sys.stderr)
        return 1
    print(f"policy ok, memory {total:.0f} MiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
