"""Bootstrap CIs for pack scores, paired model differences and the A1 bars
(REBALANCE-PLAN M9).

    python -m tools.nsfwv2.bootstrap RESULTS_DIR [RESULTS_DIR...] [--models M ...]
                                     [--resamples 10000] [--seed 20261108] [--out b.json]

Pack S per model = mean over cases of the mean row S (x100), exactly the board
formula. The unit of resampling is the CASE (with replacement); the same case
draw is applied to every model, so differences and bar statistics are paired.
The P2 "max <= 50" bar reports P(max over the cohort <= 50) under the joint case resample
and needs >= 0.8; the other bars need >= 0.95. A1 bars: P1 spread >= 20, at most one model >= 90, best >= 50 (rev. 2026-11-08); P2 every model
<= 50, best >= 20 (rev. 2026-11-08); per-model P2 <= P1 - 25 is checked by gate. A bar "holds with the half-width" when it holds on
>= 95 % of the draws (``robust``), not just at the point estimate.
"""
from __future__ import annotations

import argparse
import random
import sys

from . import _common as cm
from crucibleforge import report

BARS = {
    "P1": [("spread", ">=", 20.0), ("n_ge90", "<=", 1), ("best", ">=", 50.0)],
    "P2": [("max", "<=", 50.0), ("best", ">=", 20.0)],
}


def _stat(name, scores: dict):
    v = list(scores.values())
    if name == "spread":
        return max(v) - min(v)
    if name == "n_ge90":
        return sum(1 for x in v if x >= 90)
    if name in ("best", "max"):
        return max(v)
    raise KeyError(name)


def _ok(val, op, thr):
    return val >= thr if op == ">=" else val <= thr


def ci(draws, lo=0.025, hi=0.975):
    s = sorted(draws)
    a, b = cm.percentile(s, lo), cm.percentile(s, hi)
    return a, b, (b - a) / 2


def run(data: dict, resamples=10000, seed=20261108, packs=("P1", "P2"), robust_at=0.95,
        heldout=None) -> dict:
    """``heldout``: model labels to keep OUT of the tune bars / differences /
    top-2 (reported in ``res["heldout"]`` with their own CI and a place in the
    tune spread)."""
    held = {m: data[m] for m in (heldout or ()) if m in data}
    data = {m: r for m, r in data.items() if m not in held}
    hps = cm.pack_scores(held, packs)
    ps = cm.pack_scores(data, packs)
    rng = random.Random(seed)
    out = {"resamples": resamples, "seed": seed, "packs": {}}
    for p in packs:
        models = sorted(m for m in ps if p in ps[m])
        if not models:
            continue
        common = sorted(set.intersection(*[set(ps[m][p]["cases"]) for m in models]))
        n = len(common)
        if n == 0:
            continue
        cs = {m: [ps[m][p]["cases"][c] for c in common] for m in models}
        point = {m: 100 * cm.mean(cs[m]) for m in models}
        draws = {m: [] for m in models}
        for _ in range(resamples):
            idx = [rng.randrange(n) for _ in range(n)]
            for m in models:
                draws[m].append(100 * sum(cs[m][i] for i in idx) / n)
        res = {"cases": n, "models": {}, "diffs": {}, "bars": {}}
        for m in models:
            lo, hi, hw = ci(draws[m])
            res["models"][m] = {"S": round(point[m], 2), "ci_lo": round(lo, 2), "ci_hi": round(hi, 2),
                                "half_width": round(hw, 2), "cases_all": len(ps[m][p]["cases"])}
            # M7: the same pack score without the cases this model's family wrote the prose of
            ex = report.authored_cases_for(m, common)
            if ex:
                rest = [v for c, v in zip(common, cs[m]) if c not in ex]
                res["models"][m]["n_excl_authored"] = len(ex)
                res["models"][m]["S_excl_authored"] = round(100 * cm.mean(rest), 2) if rest else None
            fl = report.lineage_flags(m, common)
            if fl:
                res["models"][m]["n_lineage_flagged"] = len(fl)
                res["models"][m]["lineage_flags"] = sorted({f for v in fl.values() for f in v})
        for i, a in enumerate(models):
            for b in models[i + 1:]:
                d = [x - y for x, y in zip(draws[a], draws[b])]
                lo, hi, hw = ci(d)
                res["diffs"][f"{a} - {b}"] = {
                    "diff": round(point[a] - point[b], 2), "ci_lo": round(lo, 2), "ci_hi": round(hi, 2),
                    "half_width": round(hw, 2), "p_a_gt_b": round(sum(1 for x in d if x > 0) / len(d), 4),
                    "excludes_zero": lo > 0 or hi < 0}
        for name, op, thr in BARS[p]:
            if name in ("spread",) and len(models) < 2:
                continue
            vals = [_stat(name, {m: draws[m][k] for m in models}) for k in range(resamples)]
            pt = _stat(name, point)
            lo, hi, hw = ci(vals)
            share = sum(1 for v in vals if _ok(v, op, thr)) / resamples
            need = 0.8 if (p == "P2" and name == "max") else robust_at
            res["bars"][f"{name}{op}{thr:g}"] = {
                "required_share": need,
                "point": round(pt, 2), "ci_lo": round(lo, 2), "ci_hi": round(hi, 2),
                "half_width": round(hw, 2), "holds_point": _ok(pt, op, thr),
                "share_of_draws_holding": round(share, 4), "robust": share >= need}
        if len(models) >= 2:
            # rank stability: share of draws in which the point-estimate top-2 order holds
            top = sorted(models, key=lambda m: -point[m])[:2]
            res["top2"] = {"order": top, "holds": round(sum(
                1 for k in range(resamples) if draws[top[0]][k] > draws[top[1]][k]) / resamples, 4)}
        hrng = random.Random(seed + 1)
        res["heldout"] = {}
        for hm, hp in hps.items():
            if p not in hp:
                continue
            hc = sorted(set(hp[p]["cases"]) & set(common))
            if not hc:
                continue
            hv = [hp[p]["cases"][c] for c in hc]
            hd = []
            for _ in range(resamples):
                idx = [hrng.randrange(len(hc)) for _ in range(len(hc))]
                hd.append(100 * sum(hv[i] for i in idx) / len(hc))
            lo, hi, hw = ci(hd)
            S_h = 100 * cm.mean(hv)
            tune_on_hc = {m: 100 * cm.mean([ps[m][p]["cases"][c] for c in hc]) for m in models}
            ent = {"S": round(S_h, 2), "ci_lo": round(lo, 2), "ci_hi": round(hi, 2),
                   "half_width": round(hw, 2), "cases": len(hc), "cases_all": len(hp[p]["cases"])}
            ex = report.authored_cases_for(hm, hc)
            if ex:
                rest = [v for c, v in zip(hc, hv) if c not in ex]
                ent.update(n_excl_authored=len(ex),
                           S_excl_authored=round(100 * cm.mean(rest), 2) if rest else None)
            fl = report.lineage_flags(hm, hc)
            if fl:
                ent["n_lineage_flagged"] = len(fl)
            if tune_on_hc:
                ent["tune_separation"] = {
                    "tune_min": round(min(tune_on_hc.values()), 2), "tune_max": round(max(tune_on_hc.values()), 2),
                    "tune_models_above": sum(1 for x in tune_on_hc.values() if x > S_h),
                    "n_tune": len(tune_on_hc)}
            res["heldout"][hm] = ent
        out["packs"][p] = res
    return out


def render_md(res: dict) -> str:
    L = [f"# Bootstrap ({res['resamples']} resamples over cases, seed {res['seed']})", ""]
    for p, r in res["packs"].items():
        L += [f"## {p} ({r['cases']} cases)", "",
              "| model | S | 95% CI | half-width | S excl. own-prose cases |", "|---|---|---|---|---|"]
        for m, v in r["models"].items():
            ex = ("" if "n_excl_authored" not in v else
                  f"{'-' if v['S_excl_authored'] is None else format(v['S_excl_authored'], '.1f')} "
                  f"({v['n_excl_authored']} excluded)")
            if v.get("n_lineage_flagged"):
                ex += f" [{v['n_lineage_flagged']} lineage-flagged: {', '.join(v['lineage_flags'])}]"
            L.append(f"| {m} | {v['S']:.1f} | {v['ci_lo']:.1f} .. {v['ci_hi']:.1f} | +-{v['half_width']:.1f} | {ex} |")
        if r.get("heldout"):
            L += ["", "Held-out subjects (not in the tune bars, differences or top-2):", "",
                  "| held-out model | S | 95% CI | cases | tune min..max | tune models above |", "|---|---|---|---|---|---|"]
            for hm, h in r["heldout"].items():
                ts = h.get("tune_separation") or {}
                L.append(f"| {hm} | {h['S']:.1f} | {h['ci_lo']:.1f} .. {h['ci_hi']:.1f} | {h['cases']} | "
                         f"{ts.get('tune_min', '-')} .. {ts.get('tune_max', '-')} | "
                         f"{ts.get('tune_models_above', '-')}/{ts.get('n_tune', '-')} |")
        if r["diffs"]:
            L += ["", "| paired difference | diff | 95% CI | P(a>b) | excludes 0 |", "|---|---|---|---|---|"]
            for k, v in r["diffs"].items():
                L.append(f"| {k} | {v['diff']:+.1f} | {v['ci_lo']:+.1f} .. {v['ci_hi']:+.1f} | "
                         f"{v['p_a_gt_b']:.2f} | {v['excludes_zero']} |")
        L += ["", "| bar | point | 95% CI | holds (point) | P(holds) | needed | robust |", "|---|---|---|---|---|---|---|"]
        for k, v in r["bars"].items():
            L.append(f"| {k} | {v['point']:.1f} | {v['ci_lo']:.1f} .. {v['ci_hi']:.1f} | {v['holds_point']} | "
                     f"{v['share_of_draws_holding']:.2f} | {v['required_share']:.2f} | {v['robust']} |")
        if "top2" in r:
            L.append(f"\nTop-2 order {r['top2']['order']} holds in {r['top2']['holds']:.0%} of draws.")
        L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--resamples", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20261108)
    cm.add_common_args(ap)
    a = ap.parse_args(argv)
    cases = cm.load_cases(a.sets_dir)
    data = cm.load_dirs(a.dirs, cases, models=a.models)
    held = [m for m in data if report.is_heldout(m, cases, a.heldout)]
    res = run(data, a.resamples, a.seed, heldout=held)
    print(render_md(res))
    cm.write_out(a.out, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
