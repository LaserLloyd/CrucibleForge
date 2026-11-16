#!/usr/bin/env python3
"""Is the budget reference thinking-off? (G-Budget condition 2, A8)

    uv run python scripts/budget_thinkoff.py --results DIR [--profile bench45]
        [--labels L1 L2 ...] [--max-thought-share 0.02] [--json]

The nominal token count of `crucibleforge budget` is the median ANSWER tokens of the
profile's `budget.reference_models` (completion minus reasoning). That figure only
describes a non-thinking model when those reference runs had thinking OFF; a row that
carries reasoning tokens/text was a thinking run whose answer tokens are a different
quantity. For each reference model this counts the stored rows of the profile's budget
cases that "thought" (reasoning tokens > 0 or reasoning text) and reports its share.
A model with no such rows is `missing`, one above the share is `THINKING`; either fails.
Read only; exit 0 all reference models thinking-off, 1 not, 2 unreadable input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def thought(row: dict) -> bool:
    m = row.get("metrics") or {}
    return bool((m.get("reasoning_tokens") or 0) > 0 or (row.get("reasoning") or "").strip())


def classify(rows: list[dict], max_share: float = 0.02) -> dict:
    n = len(rows)
    if not n:
        return {"rows": 0, "thought": 0, "share": None, "verdict": "missing"}
    t = sum(1 for r in rows if thought(r))
    share = t / n
    return {"rows": n, "thought": t, "share": round(share, 3),
            "verdict": "thinking-off" if share <= max_share else "THINKING"}


def evaluate(rows_by_label: dict[str, list[dict]], labels: list[str], max_share: float = 0.02) -> dict:
    per = {l: classify(rows_by_label.get(l, []), max_share) for l in labels}
    bad = [l for l, v in per.items() if v["verdict"] != "thinking-off"]
    return {"models": per, "not_thinking_off": bad, "ok": bool(labels) and not bad}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", required=True)
    ap.add_argument("--profile", default="bench45")
    ap.add_argument("--labels", nargs="*", default=None)
    ap.add_argument("--max-thought-share", type=float, default=0.02)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        from crucibleforge import budget, profiles
        prof = profiles.load_profile(a.profile)
        labels = a.labels or list((prof.get("budget") or {}).get("reference_models") or [])
        ids = {i for cat, v in (prof.get("cases") or {}).items() if cat in budget.KIND_TO_SCORE
               for i in v or []}
        if not labels:
            raise ValueError("no reference models (profile budget.reference_models, or --labels)")
        res = Path(a.results).expanduser()
        by: dict[str, list[dict]] = {}
        for l in labels:
            f = res / f"transcripts_{l}.jsonl"
            rows = []
            if f.is_file():
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get("case_id") in ids:
                        rows.append(r)
            by[l] = rows
    except Exception as e:  # noqa: BLE001
        print(f"budget_thinkoff: {e}", file=sys.stderr)
        return 2
    rep = evaluate(by, labels, a.max_thought_share)
    if a.json:
        print(json.dumps(rep, indent=1))
    else:
        for l, v in rep["models"].items():
            print(f"{l:40} rows {v['rows']:4} thought {v['thought']:4} share {v['share']}  {v['verdict']}")
        print("budget reference: " + ("thinking-off" if rep["ok"] else
              "NOT thinking-off (" + ", ".join(rep["not_thinking_off"]) + "); "
              "re-run the reference models with thinking off before G-Budget"))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
