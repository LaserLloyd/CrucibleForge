#!/usr/bin/env python3
"""G-Reuse report check: per model and Base id, `reused` or `rerun_required` + reason.

    uv run python scripts/reuse_gate.py --results DIR [--profile bench45] [--expect N]
        [--judged CC4-second-pitch NC2-after-hours --judge-fingerprint FP] [--json]

Reads stored transcripts read-only. Admission is ONLY `report.reusable_row` (the single
reuse path, G-Reuse 6); the reason for a refusal is derived afterwards by `why_not`,
which only labels. Rules checked (exit 1 on any):
  * every (model, Base id) is `reused` or `rerun_required` with a reason (never silent);
  * `--expect N`: the reused count equals N, a number computed beforehand from the 4.1 store;
  * `--judged ... --judge-fingerprint FP`: a reused judged row carries FP, else `regrade_needed`
    counts it and the gate fails (G-Reuse 3).
Reasons: missing (no stored row) / truncated / errored / regime / measure / stamp (against the
source profile's tree) / generation (live generation view differs) / system / profile (a row of a profile the reuse block does not name).
Exit: 0 pass, 1 a rule failed, 2 unreadable input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def why_not(row: dict, prof: dict) -> str:
    """The reason `report.reusable_row(row, prof)` refused ``row`` (same order as
    `report.reuse_status`: the A0b rule, stamp against the SOURCE profile's tree and an
    equal generation view in the live tree). Only called for a refused row; a label,
    never an admission test."""
    from crucibleforge import report
    from crucibleforge.version import generation_view, live_views, row_case_stamp
    spec = prof.get("reuse") or {}
    cid = row.get("case_id")
    rp = row.get("profile")
    if rp not in (spec.get("from") or []) or cid not in (spec.get("ids") or []):
        return "profile"
    if row.get("finish_reason") == "length" or row.get("truncated"):
        return "truncated"
    if row.get("regime") != prof.get("regime"):
        return "regime"
    src = report._measure_view(rp)
    if src is None or src != {k: prof.get(k) for k in report._MEASURE_KEYS}:
        return "measure"
    tree = report._source_tree(rp)
    entry = (tree[0] if tree else {}).get(cid)
    if entry is None or row_case_stamp(row) != entry["case"]:
        return "stamp"
    live, old = live_views()[1].get(cid), tree[1].get(cid)
    if live is None or old is None or generation_view(live) != generation_view(old):
        return "generation"
    return "system"


def evaluate(rows_by_model: dict[str, list[dict]], prof: dict, base_ids: list[str], *,
             judged_ids=(), judge_fingerprint: str | None = None,
             expected_reused: int | None = None) -> dict:
    from crucibleforge import report
    out: dict[str, dict[str, dict]] = {}
    reused = rerun = regrade = rejudge = 0
    # S10 review: only STORED rows of a source profile are reuse candidates; the profile's own
    # rows (a model that re-ran a Base id under bench45) are not "reused" (reusable_row admits
    # them unconditionally, truncated ones included)
    sources = set((prof.get("reuse") or {}).get("from") or [])
    problems: list[str] = []
    for model, rows in sorted(rows_by_model.items()):
        out[model] = {}
        for cid in base_ids:
            anyp = [r for r in rows if r.get("case_id") == cid]
            mine = [r for r in anyp if r.get("profile") in sources]
            good = [r for r in mine if not r.get("error") and report.reusable_row(r, prof)]
            if good:
                ent = {"status": "reused", "rows": len(good),
                       "answered": max(str(r.get("ts") or "") for r in good)}
                if cid in judged_ids and judge_fingerprint and any(
                        r.get("judge_fingerprint") != judge_fingerprint for r in good):
                    ent["regrade_needed"] = True
                    regrade += 1
                # a reusable row whose live case grades differently counts on the board only
                # after a re-judge (S11 materialises it): said, not hidden
                if any(report.rejudge_required(r, prof) for r in good):
                    ent["rejudge_required"] = True
                    rejudge += 1
                reused += 1
            else:
                if not mine:
                    why = "profile" if anyp else "missing"   # rows, but none of a source profile
                else:
                    last = max(mine, key=lambda r: str(r.get("ts") or ""))
                    why = "errored" if last.get("error") and report.reusable_row(last, prof) else why_not(last, prof)
                ent = {"status": "rerun_required", "reason": why}
                rerun += 1
            out[model][cid] = ent
            if ent["status"] == "rerun_required" and not ent.get("reason"):
                problems.append(f"{model}/{cid}: pending without a reason")
    if expected_reused is not None and reused != expected_reused:
        problems.append(f"reused count {reused} != expected {expected_reused}")
    if regrade:
        problems.append(f"regrade_needed {regrade} (a reused judged row lacks the reference judge's fingerprint)")
    return {"models": out, "reused": reused, "rerun_required": rerun, "regrade_needed": regrade,
            "rejudge_required": rejudge,
            "problems": problems, "ok": not problems}


def load_rows(results: Path, ids: list[str], profiles: list[str]) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    for f in sorted(results.glob("transcripts_*.jsonl")):
        rows = []
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("case_id") in ids and r.get("profile") in profiles:
                rows.append(r)
        by[f.name[len("transcripts_"):-len(".jsonl")]] = rows
    return by


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", required=True)
    ap.add_argument("--profile", default="bench45")
    ap.add_argument("--expect", type=int, default=None, help="the reused count computed beforehand")
    ap.add_argument("--judged", nargs="*", default=[], metavar="ID")
    ap.add_argument("--judge-fingerprint", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        from crucibleforge import profiles
        profiles.set_active(a.profile)
        prof = profiles.load_profile(a.profile)
        spec = prof.get("reuse") or {}
        ids = list(spec.get("ids") or [])
        if not ids:
            raise ValueError(f"profile {a.profile} has no reuse.ids")
        rows = load_rows(Path(a.results).expanduser(), ids, [a.profile, *(spec.get("from") or [])])
    except Exception as e:  # noqa: BLE001
        print(f"reuse_gate: {e}", file=sys.stderr)
        return 2
    rep = evaluate(rows, prof, ids, judged_ids=set(a.judged), judge_fingerprint=a.judge_fingerprint,
                   expected_reused=a.expect)
    if a.json:
        print(json.dumps(rep, indent=1))
    else:
        why: dict[str, int] = {}
        for m in rep["models"].values():
            for e in m.values():
                if e["status"] == "rerun_required":
                    why[e["reason"]] = why.get(e["reason"], 0) + 1
        print(f"G-Reuse: {len(rep['models'])} models x {len(ids)} Base ids: reused {rep['reused']}, "
              f"rerun_required {rep['rerun_required']} {why or ''}; of the reused, "
              f"{rep['rejudge_required']} need a re-judge before the board counts them")
        for p in rep["problems"]:
            print("  FAIL " + p)
        print("G-Reuse: " + ("PASS" if rep["ok"] else "FAIL"))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
