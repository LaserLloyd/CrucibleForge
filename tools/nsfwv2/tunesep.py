"""M13 tune-separation statistic (REBALANCE-PLAN section 1 M13 / section 8 item 8).

    python -m tools.nsfwv2.tunesep RESULTS_DIR [RESULTS_DIR...] --tunes ID [ID...]
           [--heldout PREFIX...] [--resamples 10000] [--seed 20261108]
           [--sets-dir SETS] [--out t.json]

Computed over the TUNE models only (ids are prefixes of the model labels in the
results; hosted and held-out models are not tunes and never enter a number):
 a  P1 top-to-bottom gap in pack S >= 2 x the bootstrap half-width (the widest
    pack-score half-width among the tunes) AND gap >= 15 points
 b  paired case-bootstrap P(top > bottom) >= 0.9 (top / bottom = the point-estimate
    extremes; the same case draw is applied to every model)
 c  share of tune pairs whose 95 % CIs do not overlap >= 0.6
 d  share of P1 items that are live (0 < p < 1) on the tunes alone >= 0.4
    (itemstats' item_table over the tune rows)
 e  held-out row(s) printed beneath with where they fall in the tune ordering
    (never counted in a-d).
Case fields: ``meta.shared_data`` (list, e.g. ["readyart"]) marks shared training
data and ``meta.authorship.heldout`` lists held-out subjects; both are read by
``crucibleforge.report`` and need nothing here. Markdown on stdout, JSON via --out.
"""
from __future__ import annotations

import argparse
import sys

from crucibleforge import report

from . import _common as cm
from . import bootstrap as bs
from . import itemstats

MIN_GAP, HW_MULT, P_TOP, NO_OVERLAP, LIVE = 15.0, 2.0, 0.9, 0.6, 0.4


def select(data: dict, ids) -> dict:
    return {m: r for m, r in data.items() if report.model_matches(m, ids)}


def run(data: dict, tunes, heldout=(), resamples=10000, seed=20261108, cases=None) -> dict:
    tune = select(data, tunes)
    held = {m: r for m, r in data.items() if m not in tune and report.is_heldout(m, cases, heldout)}
    out = {"tunes": sorted(tune), "heldout": sorted(held), "checks": {}, "pass": None}
    if len(tune) < 2:
        out["error"] = f"need >= 2 tune models, found {len(tune)}"
        return out
    res = bs.run({**tune, **held}, resamples, seed, packs=("P1",), heldout=list(held))
    r = res["packs"].get("P1")
    if not r:
        out["error"] = "no P1 scores"
        return out
    ms = r["models"]
    order = sorted(ms, key=lambda m: -ms[m]["S"])
    top, bot = order[0], order[-1]
    gap = ms[top]["S"] - ms[bot]["S"]
    hw = max(v["half_width"] for v in ms.values())
    out["order"] = [{"model": m, "S": ms[m]["S"], "ci_lo": ms[m]["ci_lo"], "ci_hi": ms[m]["ci_hi"]} for m in order]
    out["checks"]["a_gap"] = {"gap": round(gap, 2), "half_width": hw, "need_gap": max(MIN_GAP, HW_MULT * hw),
                              "pass": gap >= HW_MULT * hw and gap >= MIN_GAP}
    d = r["diffs"].get(f"{top} - {bot}")
    p_top = d["p_a_gt_b"] if d else 1 - r["diffs"][f"{bot} - {top}"]["p_a_gt_b"]
    out["checks"]["b_p_top_gt_bottom"] = {"top": top, "bottom": bot, "p": round(p_top, 4), "pass": p_top >= P_TOP}
    pairs = [(a, b) for i, a in enumerate(order) for b in order[i + 1:]]
    sep = sum(1 for a, b in pairs if ms[a]["ci_lo"] > ms[b]["ci_hi"])
    share = sep / len(pairs)
    out["checks"]["c_nonoverlap_pairs"] = {"pairs": len(pairs), "separated": sep, "share": round(share, 3),
                                           "pass": share >= NO_OVERLAP}
    tab = itemstats.item_table(tune)
    items = [v for c in tab.values() if c["pack"] == "P1" for v in c["items"].values()]
    live = sum(1 for v in items if 0 < v["p"] < 1)
    ls = live / len(items) if items else 0.0
    out["checks"]["d_live_items"] = {"items": len(items), "live": live, "share": round(ls, 3), "pass": ls >= LIVE}
    out["heldout_rows"] = []
    for hm, h in (r.get("heldout") or {}).items():
        above = sum(1 for m in order if ms[m]["S"] > h["S"])
        out["heldout_rows"].append({"model": hm, "S": h["S"], "ci_lo": h["ci_lo"], "ci_hi": h["ci_hi"],
                                    "tune_models_above": above, "n_tune": len(order),
                                    "rank_in_tunes": above + 1})
    out["pass"] = all(c["pass"] for c in out["checks"].values())
    return out


def render_md(o: dict) -> str:
    L = ["# Tune separation (M13)", ""]
    if o.get("error"):
        return "\n".join(L + [f"NOT COMPUTED: {o['error']}"])
    L += ["| tune | P1 S | 95% CI |", "|---|---|---|"]
    L += [f"| {x['model']} | {x['S']:.1f} | {x['ci_lo']:.1f} .. {x['ci_hi']:.1f} |" for x in o["order"]]
    c = o["checks"]
    pf = lambda b: "PASS" if b else "FAIL"  # noqa: E731
    L += ["", "| check | value | result |", "|---|---|---|",
          f"| a gap >= max(15, 2 x half-width) | gap {c['a_gap']['gap']:.1f}, half-width {c['a_gap']['half_width']:.1f} | {pf(c['a_gap']['pass'])} |",
          f"| b P(top > bottom) >= 0.9 | {c['b_p_top_gt_bottom']['p']:.3f} ({c['b_p_top_gt_bottom']['top']} vs {c['b_p_top_gt_bottom']['bottom']}) | {pf(c['b_p_top_gt_bottom']['pass'])} |",
          f"| c CI-separated pairs >= 0.6 | {c['c_nonoverlap_pairs']['separated']}/{c['c_nonoverlap_pairs']['pairs']} ({c['c_nonoverlap_pairs']['share']:.0%}) | {pf(c['c_nonoverlap_pairs']['pass'])} |",
          f"| d live P1 items >= 0.4 | {c['d_live_items']['live']}/{c['d_live_items']['items']} ({c['d_live_items']['share']:.0%}) | {pf(c['d_live_items']['pass'])} |",
          "", f"Overall: {pf(o['pass'])}"]
    if o["heldout_rows"]:
        L += ["", "Held-out (not counted above):", "", "| held-out | P1 S | 95% CI | place among tunes |", "|---|---|---|---|"]
        L += [f"| {h['model']} | {h['S']:.1f} | {h['ci_lo']:.1f} .. {h['ci_hi']:.1f} | "
              f"{h['rank_in_tunes']} of {h['n_tune'] + 1} ({h['tune_models_above']} tunes above) |"
              for h in o["heldout_rows"]]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--tunes", nargs="+", required=True)
    ap.add_argument("--resamples", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20261108)
    cm.add_common_args(ap)
    a = ap.parse_args(argv)
    cases = cm.load_cases(a.sets_dir)
    data = cm.load_dirs(a.dirs, cases, models=a.models)
    o = run(data, a.tunes, a.heldout, a.resamples, a.seed, cases)
    print(render_md(o))
    cm.write_out(a.out, o)
    return 0 if o.get("pass") else 1


if __name__ == "__main__":
    sys.exit(main())
