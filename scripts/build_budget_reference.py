#!/usr/bin/env python3
"""Rebuild budget/reference.json (the committed digest `crucibleforge budget` reads)
from a results directory. Public case ids and numbers only.

    uv run python scripts/build_budget_reference.py --results path/to/results-snapshot \
        [--profile bench45] [--out budget/reference.json] [--min-cases 20]

Never point it at the live results dir of a running bench; copy a snapshot first."""
import argparse
import json
from pathlib import Path

from crucibleforge import budget, profiles

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", required=True)
    ap.add_argument("--profile", default="bench45")
    ap.add_argument("--out", default=str(ROOT / budget.DEFAULT_REFERENCE))
    ap.add_argument("--min-cases", type=int, default=20)
    a = ap.parse_args()
    prof = profiles.load_profile(a.profile)
    ref = budget.build_reference(Path(a.results), prof, min_cases=a.min_cases)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ref, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    print(f"{out}: {len(ref['models'])} models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
