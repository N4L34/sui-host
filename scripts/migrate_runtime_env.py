#!/usr/bin/env python3
"""Move the commands runtime config out of the public repository checkout."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def migrate(source: Path, target: Path) -> None:
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_name(target.name + ".tmp")
            try:
                shutil.copyfile(source, temporary)
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        if not target.is_file():
            raise RuntimeError(f"runtime config target is not a file: {target}")
        source.unlink()

    if not target.is_file():
        raise RuntimeError(f"missing host-only runtime config: {target}")
    os.chmod(target, 0o600)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: migrate_runtime_env.py SOURCE TARGET", file=sys.stderr)
        return 2
    migrate(Path(sys.argv[1]), Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
