#!/usr/bin/env python3
"""check_schemas.py -- validate that every schema in references/schemas/ is
itself a valid Draft 2020-12 JSON Schema. CI gate + local sanity check.

Usage: python scripts/check_schemas.py [schemas_dir]
Exit: 0 all valid, 1 any invalid.
"""
import json
import pathlib
import sys

from jsonschema import Draft202012Validator


def main() -> int:
    schemas_dir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else \
        pathlib.Path(__file__).resolve().parent.parent / "references" / "schemas"
    if not schemas_dir.is_dir():
        print(f"no schemas dir: {schemas_dir}")
        return 1
    bad = 0
    for p in sorted(schemas_dir.glob("*.json")):
        try:
            s = json.loads(p.read_text())
            Draft202012Validator.check_schema(s)
            print(f"schema ok: {p.name}")
        except Exception as e:
            bad += 1
            print(f"schema FAIL: {p.name}: {e}")
    print(f"{bad} invalid" if bad else "all schemas valid")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
