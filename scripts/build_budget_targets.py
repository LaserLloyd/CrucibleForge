#!/usr/bin/env python3
"""Rebuild budget/targets-45.json from the WP-03 case specs (target tokens, judge
calls, per-case ids/scores) and the WP-01 targets (prefill seconds, hard-set
placeholders). A case whose 4.5 rewrite is not in the tree yet (WP-03
`case_hash_changes`) gets `pending_from_hash` = its current hash, so 4.1 rows
of the old content are not mistaken for a measurement of the new case.

    uv run python scripts/build_budget_targets.py --specs case-specs-45.json \
        --wp01 budget-targets-45.json [--out budget/targets-45.json]
"""
import argparse
import json
from pathlib import Path

from crucibleforge import budget, version

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--specs", required=True)
    ap.add_argument("--wp01", required=True)
    ap.add_argument("--out", default=str(ROOT / "budget" / "targets-45.json"))
    a = ap.parse_args()
    specs = json.loads(Path(a.specs).read_text(encoding="utf-8"))
    wp01 = json.loads(Path(a.wp01).read_text(encoding="utf-8"))
    prefill = {c["case_id"]: c["prefill_s_45"] for c in wp01["cases"]}
    cases = {}
    for s in specs["slots"]:
        cid = s["case_id"]
        e = {"score": s["score"], "turns": s["turns_45"], "target_tokens": round(s["target_k"] * 1000),
             "judge_calls": s["judge_calls"], "prefill_s": prefill.get(cid, 0.0)}
        if s.get("case_hash_changes"):
            h = version.case_hash(cid)
            if h:
                e["pending_from_hash"] = h
        cases[cid] = e
    hard = {}
    for h in wp01["hard"]:
        sc = h["set"].split("_")[0]
        hard[sc] = {"cases": h["cases"], "target_tokens": h["target_tokens"], "judge_calls": h["judge_calls"]}
    out = {"schema": budget.SCHEMA_TARGETS, "revision": "4.5",
           "note": "design targets (PLAN-45-v3 2.3/2.4 as refined by WP-03 case specs); a retarget_note on a "
                   "case excuses a MEASURED/PROXY overshoot of more than 25%",
           "cases": cases, "hard": hard}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"{a.out}: {len(cases)} cases, {sum(c['target_tokens'] for c in cases.values())/1000:.1f}k tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
