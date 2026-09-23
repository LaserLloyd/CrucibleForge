#!/usr/bin/env python3
"""check_stamp.py — confirm a meta_<label>.json round-trips its three new
fields (model_version_resolved / test_date_utc / vendor_probe_source) through
the crucibleforge renderer. SKILL.md "Model version + date annotation"
(maintainer, 2026-09-09) covers the rule; this script is the smoke check.

Usage: python3 scripts/check_stamp.py <meta_label>
       (default label: deepseek-flash — the canonical case)

Exit codes:
  0  meta has all three fields; render produced `v<ver> on <date>`.
  1  meta missing one or more fields.
  2  meta invalid JSON.
  3  render did not pick up the fields (renderer regression).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def check(label: str) -> int:
    meta_p = REPO / "results" / f"meta_{label}.json"
    if not meta_p.exists():
        print(f"FAIL: {meta_p} not found", file=sys.stderr)
        return 1
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FAIL: {meta_p} invalid JSON: {e}", file=sys.stderr)
        return 2
    needed = ("model_version_resolved", "test_date_utc", "vendor_probe_source")
    for k in needed:
        if not meta.get(k):
            print(f"FAIL: {meta_p} missing {k}", file=sys.stderr)
            return 1
    from crucibleforge import report
    label_cell = report._version_label(label, {"meta": meta})
    # An opaque version (resolved == bare alias) renders `v?` per brief rule 4
    # — the date still carries the load, so check the date is on the cell
    # regardless of the version glyph.
    if meta["test_date_utc"] not in label_cell:
        print(f"FAIL: renderer dropped the date — got {label_cell!r}", file=sys.stderr)
        return 3
    print(f"OK: {label} → {label_cell!r}")
    return 0


if __name__ == "__main__":
    sys.exit(check(sys.argv[1] if len(sys.argv) > 1 else "deepseek-flash"))
