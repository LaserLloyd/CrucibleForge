#!/usr/bin/env python3
"""check_stamp.py — confirm a meta_<label>.json carries a vendor version stamp
the board will show.

The orchestrator writes results/_stamp.json BEFORE a run with FOUR fields:
``model_id`` (the exact registry model_id it probed), ``model_version_resolved``,
``test_date_utc`` and ``vendor_probe_source``. The runner copies the last
three into meta_<label>.json ONLY when ``model_id`` matches the benched model
(2026-09-23: a stamp without it — the 2026-09-09 DeepSeek probe — used to be
copied onto every model on the board). The version then appears in the
scorecard's Notes column, never on the Model cell, and only when it says more
than the bare model id.

Usage: python3 scripts/check_stamp.py <label> [--results DIR]

Exit codes:
  0  meta has the stamp; Notes will show it (or it is opaque = bare id, shown as nothing).
  1  meta missing one or more fields.
  2  meta invalid JSON.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def check(label: str, results: Path) -> int:
    meta_p = results / f"meta_{label}.json"
    if not meta_p.exists():
        print(f"FAIL: {meta_p} not found", file=sys.stderr)
        return 1
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FAIL: {meta_p} invalid JSON: {e}", file=sys.stderr)
        return 2
    for k in ("model_version_resolved", "test_date_utc", "vendor_probe_source"):
        if not meta.get(k):
            print(f"FAIL: {meta_p} missing {k} (was _stamp.json written with this "
                  f"model's model_id?)", file=sys.stderr)
            return 1
    from crucibleforge import report
    note = report.version_note({"meta": meta})
    print(f"OK: {label} → Notes: {note!r}" if note else
          f"OK: {label} → opaque version (bare id) — nothing shown")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    res = REPO / "results"
    if "--results" in args:
        i = args.index("--results")
        res = Path(args[i + 1])
        del args[i:i + 2]
    sys.exit(check(args[0] if args else "deepseek-flash", res))
