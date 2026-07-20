from __future__ import annotations

import sys
from pathlib import Path


def _read_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            raise ValueError(f"{path.name}: unpinned requirement: {line}")
        name, version = line.split("==", 1)
        pins[name.lower()] = version
    return pins


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    requirements = _read_pins(root / "requirements.txt")
    lock = _read_pins(root / "requirements.lock")
    if requirements != lock:
        print("requirements.txt and requirements.lock differ", file=sys.stderr)
        return 1
    print("OK: dependency declarations match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
