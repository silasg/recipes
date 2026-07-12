#!/usr/bin/env python3
"""Ensure the fork's default-unit translation keys survive an upstream locale merge.

Upstream frequently reindents whole locale files, which forces git to flag the entire
file as conflicted. The safe resolution is: take upstream's version, then re-insert the
fork's two keys. This script does exactly that, idempotently and while preserving
upstream's formatting (line-based insert after a stable anchor key).

Exit codes:
  0  keys present (inserted or already there)
  3  anchor key not found in a file that still needs a key -> caller should escalate
"""
import json
import sys

ANCHOR = "DefaultShoppingListHelp"  # key that alphabetically precedes DefaultUnit
KEYS = {
    "vue3/src/locales/en.json": [
        ('DefaultUnit', 'Default Unit'),
        ('DefaultUnitHelp', 'Unit used for property calculations when ingredients have no unit specified.'),
    ],
    "vue3/src/locales/de.json": [
        ('DefaultUnit', 'Standardeinheit'),
        ('DefaultUnitHelp', 'Einheit für Nährwertberechnungen, wenn Zutaten keine Einheit haben.'),
    ],
}


def ensure(path, pairs):
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()

    missing = [(k, v) for k, v in pairs if not any(f'"{k}":' in ln for ln in lines)]
    if not missing:
        return True

    anchor_idx = next((i for i, ln in enumerate(lines) if f'"{ANCHOR}":' in ln), None)
    if anchor_idx is None:
        print(f"ERROR: anchor '{ANCHOR}' not found in {path}", file=sys.stderr)
        return False

    indent = lines[anchor_idx][: len(lines[anchor_idx]) - len(lines[anchor_idx].lstrip())]
    new_lines = [f'{indent}{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},\n' for k, v in missing]
    lines[anchor_idx + 1 : anchor_idx + 1] = new_lines

    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)

    json.load(open(path, encoding="utf-8"))  # raises if we broke the file
    print(f"inserted {[k for k, _ in missing]} into {path}")
    return True


if __name__ == "__main__":
    ok = all(ensure(path, pairs) for path, pairs in KEYS.items())
    sys.exit(0 if ok else 3)
