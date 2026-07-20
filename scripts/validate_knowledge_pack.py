#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.knowledge_pack import validate_pack


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a knowledge pack strictly without changing runtime fail-soft behavior."
    )
    parser.add_argument("pack_dir", help="knowledge pack directory")
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    parser.add_argument("--exercise", action="store_true", help="run fake terminal conversation")
    args = parser.parse_args()

    pack = Path(args.pack_dir)
    missing_pack = not pack.exists() or not pack.is_dir()
    result = validate_pack(pack, exercise=args.exercise)
    payload = result.as_dict()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(f"pack={payload['pack']} valid={str(payload['valid']).lower()} errors={len(result.errors)} warnings={len(result.warnings)}")
        for issue in result.errors:
            print(f"ERROR {issue.code} {issue.path}: {issue.message}")
        for issue in result.warnings:
            print(f"WARNING {issue.code} {issue.path}: {issue.message}")

    if result.valid:
        return 0
    return 2 if missing_pack else 1


if __name__ == "__main__":
    raise SystemExit(main())
