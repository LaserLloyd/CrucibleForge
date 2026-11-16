"""Per-item statistics over model x rep rows (REBALANCE-PLAN M2).

    python -m tools.nsfwv2.itemstats RESULTS_DIR [RESULTS_DIR...] [--min-models N] [--out items.json]

Per case and item: p (pass rate), n, r_it (corrected item-total correlation:
item credit vs the mean credit of the OTHER items of the same row,
point-biserial), hi-lo discrimination (pass rate of the top half of models by
case C minus the bottom half) and flags dead / low_disc / unstable / neg_rit.
Only ENGAGED rows (W >= 3) are used: a refusal zeroes every element and would
make every item look discriminating. Ids and numbers only, never text.
"""
from __future__ import annotations

import argparse
import math
import sys

from . import _common as cm


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def item_table(data: dict, min_models: int = 8, unstable_share: float = 0.30) -> dict:
    """data: {model: [rows]} -> {case: {'pack','models','n_rows','items': {id: stats}}}."""
    cases = {}
    refused = {}
    for m, rows in data.items():
        for r in rows:
            if r["engaged"] and r["items"]:
                cases.setdefault(r["case"], []).append((m, r))
            elif not r["engaged"]:
                refused[r["case"]] = refused.get(r["case"], 0) + 1
    out = {}
    for cid, mrs in sorted(cases.items()):
        models = sorted({m for m, _ in mrs})
        cmean = {}
        for m in models:
            cmean[m] = cm.mean([r["c"] for mm, r in mrs if mm == m and r["c"] is not None])
        ranked = sorted([m for m in models if cmean[m] is not None], key=lambda m: cmean[m])
        k = len(ranked) // 2
        lo = set(ranked[:k])
        hi = set(ranked[len(ranked) - k:]) if k else set()
        ids = sorted({i for _, r in mrs for i in r["items"]})
        items = {}
        for i in ids:
            xs, ys, passes, split, with_votes = [], [], [], 0, 0
            per_model = {}
            for m, r in mrs:
                it = r["items"].get(i)
                if it is None:
                    continue
                others = [v["credit"] for j, v in r["items"].items() if j != i]
                pv = 1.0 if it["pass"] else 0.0
                passes.append(pv)
                per_model.setdefault(m, []).append(pv)
                if others:
                    xs.append(it["credit"])
                    ys.append(sum(others) / len(others))
                if it["yes"] and len(it["yes"]) >= 3:
                    with_votes += 1
                    s = sum(it["yes"])
                    split += 0 < s < len(it["yes"])
            if not passes:
                continue
            p = sum(passes) / len(passes)
            r_it = pearson(xs, ys)
            disc = None
            if lo and hi:
                a = [v for m in hi for v in per_model.get(m, [])]
                b = [v for m in lo for v in per_model.get(m, [])]
                if a and b:
                    disc = cm.mean(a) - cm.mean(b)
            flags = []
            guard = any(r["items"].get(i, {}).get("guard") for _, r in mrs)
            if (p == 0.0) or (p == 1.0 and not guard):
                flags.append("dead")
            if r_it is not None and r_it < 0.2 and len(models) >= min_models:
                flags.append("low_disc")
            if r_it is not None and r_it < 0 and len(models) >= min_models:
                flags.append("neg_rit")
            if with_votes and split / with_votes > unstable_share:
                flags.append("unstable")
            items[i] = {"p": round(p, 4), "n": len(passes),
                        "r_it": None if r_it is None else round(r_it, 3),
                        "hi_lo": None if disc is None else round(disc, 3),
                        "split_share": None if not with_votes else round(split / with_votes, 3),
                        "guard": guard,
                        "flags": flags}
        out[cid] = {"pack": mrs[0][1]["pack"], "models": models, "n_rows": len(mrs),
                     "n_refused_rows": refused.get(cid, 0), "items": items}
    return out


def render_md(table: dict, min_models: int) -> str:
    lines = ["# Item statistics", "",
             f"Engaged rows only (unengaged reps counted per case, not used). Guard items (named guard checks, forbid_regex, negative elements) are dead only when they fail for everyone. low_disc needs >= {min_models} models per case (flags, not rules, below that).", ""]
    tot = {"dead": 0, "low_disc": 0, "unstable": 0, "neg_rit": 0, "items": 0}
    for cid, c in table.items():
        its = c["items"]
        fl = {k: [i for i, v in its.items() if k in v["flags"]] for k in ("dead", "low_disc", "neg_rit", "unstable")}
        live = [i for i, v in its.items() if "dead" not in v["flags"]]
        tot["items"] += len(its)
        for k in fl:
            tot[k] += len(fl[k])
        lines.append(f"## {cid} ({c['pack']}): {len(its)} items, {len(c['models'])} models, "
                     f"{c['n_rows']} engaged rows ({c['n_refused_rows']} unengaged reps left out), {len(live)} live")
        for k, v in fl.items():
            if v:
                lines.append(f"- {k} ({len(v)}): {', '.join(v)}")
        lines += ["", "| item | p | n | r_it | hi-lo | flags |", "|---|---|---|---|---|---|"]
        for i, v in its.items():
            def f(x):
                return "-" if x is None else f"{x:.2f}"
            lines.append(f"| {i} | {v['p']:.2f} | {v['n']} | {f(v['r_it'])} | {f(v['hi_lo'])} | {' '.join(v['flags'])} |")
        lines.append("")
    lines.insert(2, f"Totals: {tot['items']} items, dead {tot['dead']}, low_disc {tot['low_disc']}, "
                    f"neg_rit {tot['neg_rit']}, unstable {tot['unstable']}.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--min-models", type=int, default=8)
    cm.add_common_args(ap)
    a = ap.parse_args(argv)
    cases = cm.load_cases(a.sets_dir)
    data = cm.load_dirs(a.dirs, cases, models=a.models)
    data, held = cm.split_heldout(data, cases, a.heldout)     # held-out subjects never pooled
    table = item_table(data, a.min_models)
    print(render_md(table, a.min_models))
    cm.write_out(a.out, {"min_models": a.min_models, "models": sorted(data), "heldout_excluded": sorted(held), "cases": table})
    return 0


if __name__ == "__main__":
    sys.exit(main())
