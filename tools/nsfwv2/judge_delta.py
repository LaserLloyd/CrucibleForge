"""Per-model judge delta (self-preference check): the same transcripts judged by
two judges; per model per pack S_A - S_B with a paired case bootstrap CI.
Flag |delta| > 5.

    python -m tools.nsfwv2.judge_delta DIR_A DIR_B [--threshold 5] [--resamples 10000] [--seed N] [--out d.json]

Only (case, repeat) rows both dirs scored are compared; the bootstrap resamples
cases with replacement (the same draw for A and B).
"""
from __future__ import annotations

import argparse
import random
import sys

from . import _common as cm


def compare(da, db, threshold=5.0, resamples=10000, seed=20261108) -> dict:
    rng = random.Random(seed)
    out = {"threshold": threshold, "rows": []}
    for m in sorted(set(da) & set(db)):
        ia = {r["pair"]: r for r in da[m]}
        ib = {r["pair"]: r for r in db[m]}
        common = set(ia) & set(ib)
        for p in ("P1", "P2"):
            ca = cm.case_scores([ia[k] for k in common if ia[k]["pack"] == p])
            cb = cm.case_scores([ib[k] for k in common if ib[k]["pack"] == p])
            cs = sorted(set(ca) & set(cb))
            if not cs:
                continue
            diffs = [100 * (ca[c] - cb[c]) for c in cs]
            n = len(cs)
            draws = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
            lo, hi = cm.percentile(draws, 0.025), cm.percentile(draws, 0.975)
            d = sum(diffs) / n
            out["rows"].append({"model": m, "pack": p, "cases": n, "S_A": round(100 * cm.mean([ca[c] for c in cs]), 2),
                                "S_B": round(100 * cm.mean([cb[c] for c in cs]), 2), "delta": round(d, 2),
                                "ci_lo": round(lo, 2), "ci_hi": round(hi, 2),
                                "flag": abs(d) > threshold, "ci_excludes_0": lo > 0 or hi < 0})
    out["n_flagged"] = sum(1 for r in out["rows"] if r["flag"])
    return out


def render_md(r):
    L = ["# Judge delta (A - B)", "", f"Flag |delta| > {r['threshold']}; flagged {r['n_flagged']} of {len(r['rows'])}.", "",
         "| model | pack | S_A | S_B | delta | 95% CI | excludes 0 | flag |", "|---|---|---|---|---|---|---|---|"]
    for x in r["rows"]:
        L.append(f"| {x['model']} | {x['pack']} | {x['S_A']:.1f} | {x['S_B']:.1f} | {x['delta']:+.1f} | "
                 f"{x['ci_lo']:+.1f} .. {x['ci_hi']:+.1f} | {x['ci_excludes_0']} | {'FLAG' if x['flag'] else ''} |")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir_a")
    ap.add_argument("dir_b")
    ap.add_argument("--threshold", type=float, default=5.0)
    ap.add_argument("--resamples", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20261108)
    cm.add_common_args(ap)
    a = ap.parse_args(argv)
    cases = cm.load_cases(a.sets_dir)
    res = compare(cm.load_dirs([a.dir_a], cases, models=a.models), cm.load_dirs([a.dir_b], cases, models=a.models),
                  a.threshold, a.resamples, a.seed)
    print(render_md(res))
    cm.write_out(a.out, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
