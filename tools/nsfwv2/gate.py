"""Acceptance gate of REBALANCE-PLAN section 8, printed as PASS / FAIL / NOT CHECKED per line.

    python -m tools.nsfwv2.gate RESULTS_DIR [RESULTS_DIR...] --fitness FITNESS.md
           [--kappa kappa.json] [--itemstats items.json] [--stability stab.json]
           [--sets-dir SETS] [--resamples N] [--min-models 4]

Exit 0 only when every checked line passes (NOT CHECKED lines do not fail the run
but are listed). Lines:
 1 pack verify + lint clean            not checked here
 2 fitness PASS on 18/18               from the FITNESS.md table (rows LR/LU/LW only)
 3 no dead items on the pilot (4+ models)   from itemstats.py JSON
 4 hosted bars (point; robust = holds in >= 95 % of bootstrap draws)
 5 kappa >= 0.6 on >= 80 % of elements; re-judge stability <= 3 points
 6 authorship ledger complete (needs --sets-dir); twins untouched: not checked here
 7 rig needs written                   not checked here
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from . import _common as cm
from . import bootstrap as bs
from . import authorship as au
from . import tunesep as ts

P, F, N = "PASS", "FAIL", "NOT CHECKED"
_ROW = re.compile(r"^\|\s*(L[RUW]\d-\d)\s*\|\s*(PASS|FAIL)\s*\|")


def fitness_counts(path):
    seen = {}
    for line in open(path):
        m = _ROW.match(line)
        if m:
            seen[m.group(1)] = m.group(2)
    return seen


def evaluate(data, fitness=None, kappa=None, items=None, stability=None, sets_dir=None,
             resamples=10000, min_models=4, n_cases=18, tunes=None, heldout=(), cases=None, held_data=None) -> list[tuple]:
    L = [("1 verify + lint clean", N, "run `pack verify` and the WP8 lint separately")]
    # 2 fitness
    if fitness:
        fc = fitness_counts(fitness)
        ok = sum(1 for v in fc.values() if v == "PASS")
        fails = sorted(k for k, v in fc.items() if v != "PASS")
        L.append(("2 fitness PASS on 18/18", P if len(fc) == n_cases and ok == n_cases else F,
                  f"{ok}/{len(fc)} PASS" + (f"; failing: {', '.join(fails)}" if fails else "")))
    else:
        L.append(("2 fitness PASS on 18/18", N, "no --fitness given"))
    # 3 dead items
    if items:
        models = items.get("models") or []
        dead = {c: [i for i, v in d["items"].items() if "dead" in v["flags"]] for c, d in items["cases"].items()}
        nd = sum(len(v) for v in dead.values())
        other = sum(1 for d in items["cases"].values() for v in d["items"].values()
                    if set(v["flags"]) - {"dead"})
        if len(models) < min_models:
            L.append(("3 no dead items (4+ models)", F, f"only {len(models)} model(s); {nd} dead items"))
        else:
            worst = sorted(dead.items(), key=lambda kv: -len(kv[1]))[:3]
            L.append(("3 no dead items (4+ models)", P if nd == 0 else F,
                      f"{nd} dead items over {len(models)} models; {other} other flagged items need a decision"
                      + ("" if nd == 0 else "; most: " + ", ".join(f"{c}={len(v)}" for c, v in worst))))
    else:
        L.append(("3 no dead items (4+ models)", N, "no --itemstats given"))
    # 4 bars
    res = bs.run(data, resamples)
    for pk in ("P1", "P2"):
        r = res["packs"].get(pk)
        if not r:
            L.append((f"4 {pk} bars", F, "no scored cases"))
            continue
        for name, b in r["bars"].items():
            L.append((f"4 {pk} {name}", P if b["holds_point"] else F,
                      f"point {b['point']:.1f}, 95% CI {b['ci_lo']:.1f}..{b['ci_hi']:.1f} "
                      f"(half-width {b['half_width']:.1f}); holds in {b['share_of_draws_holding']:.0%} of draws"
                      f"{'' if b['robust'] else ' (not robust)'}"))
    # 5 kappa + stability
    if kappa:
        if kappa.get("rated_elements"):
            sh = kappa["rated_ok_share"]
            L.append(("5a kappa >= 0.6 on >= 80% of elements", P if sh >= 0.8 else F,
                      f"{sh:.0%} of {kappa['rated_elements']} elements with >= {kappa['min_pairs']} pairs"))
        else:
            cs = kappa.get("per_case") or {}
            ok = sum(1 for v in cs.values() if v["kappa"] is None or v["kappa"] >= 0.6)
            share = ok / len(cs) if cs else 0.0
            L.append(("5a kappa >= 0.6 on >= 80% of elements", P if share >= 0.8 else F,
                      f"no element has >= {kappa['min_pairs']} pairs; PROXY pooled per case: kappa >= 0.6 on "
                      f"{ok}/{len(cs)} cases (pooled {kappa['pooled']['kappa']}, {kappa['paired_decisions']} decisions)"))
    else:
        L.append(("5a kappa >= 0.6 on >= 80% of elements", N, "no --kappa given"))
    if stability:
        L.append(("5b re-judge stability <= 3 points", P if stability["n_flagged"] == 0 else F,
                  f"max delta {stability['max_delta']}, {stability['n_flagged']} of {len(stability['rows'])} pack scores flagged"))
    else:
        L.append(("5b re-judge stability <= 3 points", N, "no --stability given (rejudge_stability.py --out)"))
    # 6 authorship
    if sets_dir:
        bad = []
        n = 0
        for f in au.set_files(sets_dir):
            for c in json.load(open(f)).get("cases", []):
                n += 1
                pr = au.validate(((c.get("meta") or {}).get("authorship")))
                if pr:
                    bad.append(c.get("id"))
        L.append(("6a authorship ledger complete", P if n and not bad else F,
                  f"{n - len(bad)}/{n} cases complete" + (f"; missing: {', '.join(bad[:8])}" if bad else "")))
    else:
        L.append(("6a authorship ledger complete", N, "no --sets-dir given"))
    L.append(("6b twins untouched since 2026-11-08", N, "check with git/mtime on filled/twins"))
    L.append(("7 rig needs written", N, "READY-FOR-RIG.md"))
    # 8 tune separation (M13)
    if tunes:
        o = ts.run({**data, **(held_data or {})}, tunes, heldout, resamples, cases=cases)
        if o.get("error"):
            L.append(("8 tune separation (M13)", F, o["error"]))
        else:
            c = o["checks"]
            L.append(("8 tune separation (M13)", P if o["pass"] else F,
                      f"gap {c['a_gap']['gap']:.1f} (hw {c['a_gap']['half_width']:.1f}); "
                      f"P(top>bottom) {c['b_p_top_gt_bottom']['p']:.2f}; "
                      f"separated pairs {c['c_nonoverlap_pairs']['share']:.0%}; "
                      f"live P1 items {c['d_live_items']['share']:.0%}"))
    else:
        L.append(("8 tune separation (M13)", N, "no --tunes given"))
    return L


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--fitness")
    ap.add_argument("--kappa")
    ap.add_argument("--itemstats")
    ap.add_argument("--stability")
    ap.add_argument("--resamples", type=int, default=10000)
    ap.add_argument("--tunes", nargs="*", default=None)
    ap.add_argument("--min-models", type=int, default=4)
    cm.add_common_args(ap)
    a = ap.parse_args(argv)
    cases = cm.load_cases(a.sets_dir)
    data = cm.load_dirs(a.dirs, cases, models=a.models)
    data, held_data = cm.split_heldout(data, cases, a.heldout)     # tune bars only
    load = lambda p: json.load(open(p)) if p else None
    lines = evaluate(data, a.fitness, load(a.kappa), load(a.itemstats), load(a.stability),
                     a.sets_dir if a.sets_dir else None, a.resamples, a.min_models,
                     tunes=a.tunes, heldout=a.heldout, cases=cases, held_data=held_data)
    for name, st, msg in lines:
        print(f"[{st:11}] {name}: {msg}")
    failed = sum(1 for _, st, _ in lines if st == F)
    print(f"\n{'READY' if not failed else 'NOT READY'}: {failed} failing line(s), "
          f"{sum(1 for _, st, _ in lines if st == N)} not checked here.")
    cm.write_out(a.out, [{"line": n, "status": s, "detail": m} for n, s, m in lines])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
