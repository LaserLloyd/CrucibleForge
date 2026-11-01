#!/usr/bin/env python3
"""Score rpdeep rows from a results dir: fact rate per row (scored checks +
judge elements, one grader per fact), mean +/- SD per case over repeats,
per-fact-type diagnostics, craft, and the integrity counters (unjudged items,
clamped judge groups, context overflow, truncated turns).

Usage: uv run python scripts/rpdeep_score.py --results <dir> [--labels a,b] [--json out.json] [--detail]
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def load_rows(results: Path, label: str) -> list[dict]:
    p = results / f"transcripts_{label}.jsonl"
    rows = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("category") != "rpdeep":
            continue
        rows[(r.get("bench_run_id"), r.get("case_id"), r.get("repeat"), r.get("turn"))] = r
    return list(rows.values())


def score_row(row: dict, case: dict, turn_rows: list[dict]) -> dict:
    from crucibleforge.rpdeep import score_row as _score
    return _score(row, case, turn_rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--labels", default="")
    ap.add_argument("--json")
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--run-ids", default="", help="only these bench_run_ids (comma list)")
    a = ap.parse_args()
    from crucibleforge.config import load_cases
    cases = {c["id"]: c for c in load_cases(["rpdeep"])}
    res = Path(a.results)
    labels = [x for x in a.labels.split(",") if x] or sorted(
        p.name[len("transcripts_"):-len(".jsonl")] for p in res.glob("transcripts_*.jsonl"))
    runs = {x for x in a.run_ids.split(",") if x}
    out = {}
    for label in labels:
        rows = load_rows(res, label)
        if runs:
            rows = [r for r in rows if r.get("bench_run_id") in runs]
        finals = [r for r in rows if r.get("needs_judge")]
        # a session that errored mid-way never produced a final row: count it,
        # or the case mean is silently taken over the surviving repeats only
        errored = sorted({(r.get("case_id"), r.get("repeat")) for r in rows
                          if r.get("grade") == "error"}
                         - {(r.get("case_id"), r.get("repeat")) for r in finals})
        if errored:
            print(f"  WARNING {label}: errored sessions without a final row: {errored}")
        per_case = defaultdict(list)
        ftype = defaultdict(lambda: [0, 0])
        for r in finals:
            case = cases.get(r["case_id"])
            if not case:
                continue
            turns = [t for t in rows if t.get("bench_run_id") == r.get("bench_run_id")
                     and t.get("case_id") == r["case_id"] and t.get("repeat") == r.get("repeat")]
            s = score_row(r, case, turns)
            s["repeat"], s["bench_run_id"] = r.get("repeat"), r.get("bench_run_id")
            per_case[r["case_id"]].append(s)
            if s["status"] == "ok":
                for i in s["items"]:
                    if not i.get("scored", True):
                        continue
                    ftype[i["ftype"]][0] += i["weight"] * i["pass"]
                    ftype[i["ftype"]][1] += i["weight"]
        out[label] = {}
        print(f"== {label}")
        for cid, ss in sorted(per_case.items()):
            rates = [s["rate"] for s in ss if s.get("rate") is not None]
            m = statistics.mean(rates) if rates else None
            sd = statistics.stdev(rates) if len(rates) > 1 else 0.0
            crafts = [s["craft"] for s in ss if s.get("craft")]
            craft = ({k: round(statistics.mean(c[k] for c in crafts), 1)
                      for k in ("voice", "craft", "initiative")} if crafts else None)
            out[label][cid] = {"rates": rates, "mean": m, "sd": sd, "craft": craft, "rows": ss}
            incomplete = sum(1 for s in ss if s.get("rate") is None)
            if incomplete:
                print(f"  WARNING {cid}: {incomplete} row(s) incomplete (unjudged/judge error) — "
                      "re-judge before quoting this case")
            if m is None:
                print(f"  {cid}: not judged yet")
            else:
                print(f"  {cid}: fact rate {m*100:.1f} ± {sd*100:.1f} (n={len(rates)}: "
                      + ", ".join(f"{r*100:.0f}" for r in rates) + f")  craft={craft}")
            for s in ss:
                if s["status"] != "ok":
                    rx = [i for i in s["items"] if i["grader"] == "regex"]
                    print(f"    rep {s['repeat']}: {s['status']} (NOT SCORED) unjudged={s['unjudged']} "
                          f"judge_errors={s['judge_errors'][:2]} — regex items "
                          f"{sum(i['pass'] for i in rx)}/{len(rx)}")
                    if a.detail:
                        for i in rx:
                            print(f"      {'PASS' if i['pass'] else 'fail'} {i['id']:<22} {i['detail']}"[:230])
                    continue
                print(f"    rep {s['repeat']}: {s['weight_passed']:.1f}/{s['weight_total']:.1f} "
                      f"({s['passed']}/{s['n']} items) refused={s['refused']} "
                      f"overflow_possible={s['overflow_possible']} overflow_actual={s['overflow_actual']} "
                      f"truncated={s['truncated']} empty={s['empty_turns']} ooc={s['ooc_turns']} "
                      f"max_prompt_tok={s['prompt_tokens_last']} groups_clamped={s['groups_clamped']} "
                      f"craft_clamped={s['craft_clamped']} craft_error={s['craft_error']} "
                      f"judge_errors={len(s['judge_errors'])} sanity_failed={s.get('sanity_failed')}")
                if a.detail:
                    for i in s["items"]:
                        print(f"      {'PASS' if i['pass'] else 'fail'} {i['id']:<22} {i['ftype']:<11} "
                              f"{i['grader']:<5} {i.get('quote') or i['detail']}"[:230])
        print("  by fact type (diagnostic): " + ", ".join(
            f"{k} {v[0]:g}/{v[1]:g}" for k, v in sorted(ftype.items())))
        out[label]["_ftype"] = dict(ftype)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
