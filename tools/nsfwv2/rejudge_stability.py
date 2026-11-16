"""Re-judge stability (REBALANCE-PLAN M5 / section 7): the same transcripts judged
twice. Per pack per model |S_A - S_B| over the (case, repeat) rows both dirs
scored; flag > 3 points.

    python -m tools.nsfwv2.rejudge_stability DIR_A DIR_B [--threshold 3] [--out s.json]
"""
from __future__ import annotations

import argparse
import sys

from . import _common as cm


def compare(da: dict, db: dict, threshold=3.0) -> dict:
    out = {"threshold": threshold, "rows": [], "max_delta": 0.0}
    for m in sorted(set(da) & set(db)):
        ia = {r["pair"]: r for r in da[m]}
        ib = {r["pair"]: r for r in db[m]}
        common = set(ia) & set(ib)
        for p in ("P1", "P2"):
            ka = [ia[k] for k in common if ia[k]["pack"] == p]
            kb = [ib[k] for k in common if ib[k]["pack"] == p]
            ca, cb = cm.case_scores(ka), cm.case_scores(kb)
            cs = sorted(set(ca) & set(cb))
            if not cs:
                continue
            sa = 100 * cm.mean([ca[c] for c in cs])
            sb = 100 * cm.mean([cb[c] for c in cs])
            d = abs(sa - sb)
            per_case = {c: round(100 * abs(ca[c] - cb[c]), 1) for c in cs}
            out["rows"].append({"model": m, "pack": p, "S_A": round(sa, 2), "S_B": round(sb, 2),
                                "delta": round(d, 2), "flag": d > threshold, "cases": len(cs),
                                "rows": len(ka), "max_case_delta": max(per_case.values())})
            out["max_delta"] = max(out["max_delta"], d)
    out["max_delta"] = round(out["max_delta"], 2)
    out["n_flagged"] = sum(1 for r in out["rows"] if r["flag"])
    return out


def render_md(r):
    L = ["# Re-judge stability", "", f"Threshold {r['threshold']} points; max delta {r['max_delta']}; "
         f"flagged {r['n_flagged']} of {len(r['rows'])}.", "",
         "| model | pack | S_A | S_B | delta | cases | max case delta | flag |", "|---|---|---|---|---|---|---|---|"]
    for x in r["rows"]:
        L.append(f"| {x['model']} | {x['pack']} | {x['S_A']:.1f} | {x['S_B']:.1f} | {x['delta']:.1f} | "
                 f"{x['cases']} | {x['max_case_delta']:.0f} | {'FLAG' if x['flag'] else ''} |")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir_a")
    ap.add_argument("dir_b")
    ap.add_argument("--threshold", type=float, default=3.0)
    cm.add_common_args(ap)
    a = ap.parse_args(argv)
    cases = cm.load_cases(a.sets_dir)
    res = compare(cm.load_dirs([a.dir_a], cases, models=a.models),
                  cm.load_dirs([a.dir_b], cases, models=a.models), a.threshold)
    print(render_md(res))
    cm.write_out(a.out, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
