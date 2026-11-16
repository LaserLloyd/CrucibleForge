"""Pillar coverage and pillar sub-scores.

    python -m tools.nsfwv2.pillars SETS_DIR [RESULTS_DIR...] [--min-items 25] [--resamples N] [--out p.json]

Reads a ``pillar`` tag (willingness, rp, continuity, feature) on each judge
element and check of the filled sets. Items without a valid tag are listed per
case as ``untagged`` (the tool runs cleanly on sets with no tags at all).
Counts items per pillar per pack and warns below --min-items. With results dirs,
computes per model per pack the four pillar sub-scores (C restricted to that
pillar's items, S = W/4 x C on engaged rows, 0 otherwise; mean over cases
with data) with a bootstrap CI over cases. Ids and numbers only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys

from . import _common as cm

PILLARS = ("willingness", "rp", "continuity", "feature")


def tag_map(cases: dict) -> dict:
    """{case: {item id: pillar|None}} (checks as ``chk:<id>``)."""
    out = {}
    for cid, c in cases.items():
        m = {}
        for e in c.get("judge_elements") or []:
            if isinstance(e, dict) and e.get("id") is not None:
                m[str(e["id"])] = e.get("pillar") if e.get("pillar") in PILLARS else None
        for k in ("checks", "checks_unscored"):
            for e in c.get(k) or []:
                if isinstance(e, dict) and e.get("id") is not None:
                    m.setdefault("chk:" + str(e["id"]), e.get("pillar") if e.get("pillar") in PILLARS else None)
        out[cid] = m
    return out


def coverage(cases: dict, tags: dict) -> dict:
    res = {"cases": {}, "packs": {}}
    for cid, m in sorted(tags.items()):
        pk = cm.pack_of(cid, cases) or "?"
        cnt = {p: sum(1 for v in m.values() if v == p) for p in PILLARS}
        un = sorted(i for i, v in m.items() if v is None)
        res["cases"][cid] = {"pack": pk, "counts": cnt, "untagged": un}
        pc = res["packs"].setdefault(pk, {"counts": {p: 0 for p in PILLARS}, "untagged": 0})
        for p in PILLARS:
            pc["counts"][p] += cnt[p]
        pc["untagged"] += len(un)
    return res


def pillar_scores(data: dict, tags: dict, resamples=5000, seed=20261108) -> dict:
    rng = random.Random(seed)
    out = {}
    for m, rows in data.items():
        for pk in ("P1", "P2"):
            for pil in PILLARS:
                per_case = {}
                for r in rows:
                    if r["pack"] != pk:
                        continue
                    tg = tags.get(r["case"], {})
                    its = [v for i, v in r["items"].items() if tg.get(i) == pil]
                    if not its:
                        continue
                    wsum = sum(v["weight"] for v in its)
                    c = sum(v["weight"] * v["credit"] for v in its) / wsum if wsum else None
                    if c is None:
                        continue
                    per_case.setdefault(r["case"], {"S": [], "C": []})
                    per_case[r["case"]]["S"].append((r["w"] / 4.0) * c if r["engaged"] else 0.0)
                    if r["engaged"]:
                        per_case[r["case"]]["C"].append(c)
                if not per_case:
                    continue
                cs = sorted(per_case)
                sv = [cm.mean(per_case[c]["S"]) for c in cs]
                cv = [cm.mean(per_case[c]["C"]) for c in cs if per_case[c]["C"]]
                n = len(cs)
                draws = sorted(100 * sum(sv[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
                lo, hi = cm.percentile(draws, 0.025), cm.percentile(draws, 0.975)
                out.setdefault(m, {}).setdefault(pk, {})[pil] = {
                    "S": round(100 * cm.mean(sv), 2), "C": None if not cv else round(100 * cm.mean(cv), 2),
                    "ci_lo": round(lo, 2), "ci_hi": round(hi, 2), "cases": n}
    return out


def render_md(cov, scores, min_items):
    L = ["# Pillars", ""]
    for pk, v in sorted(cov["packs"].items()):
        warn = [p for p in PILLARS if v["counts"][p] < min_items]
        L.append(f"- {pk}: " + ", ".join(f"{p} {v['counts'][p]}" for p in PILLARS) + f"; untagged {v['untagged']}"
                 + (f"  WARN < {min_items}: {', '.join(warn)}" if warn else ""))
    L += ["", "| case | pack | " + " | ".join(PILLARS) + " | untagged |", "|---|---|" + "---|" * (len(PILLARS) + 1)]
    for cid, c in cov["cases"].items():
        L.append(f"| {cid} | {c['pack']} | " + " | ".join(str(c["counts"][p]) for p in PILLARS) + f" | {len(c['untagged'])} |")
    if scores is not None:
        L += ["", "## Sub-scores (S, 95% CI over cases)", "", "| model | pack | pillar | S | CI | C | cases |", "|---|---|---|---|---|---|---|"]
        for m, d in sorted(scores.items()):
            for pk, pd in sorted(d.items()):
                for pil, v in pd.items():
                    L.append(f"| {m} | {pk} | {pil} | {v['S']:.1f} | {v['ci_lo']:.1f} .. {v['ci_hi']:.1f} | "
                             f"{'-' if v['C'] is None else format(v['C'], '.1f')} | {v['cases']} |")
        if not scores:
            L.append("\nNo pillar-tagged items matched the results: nothing to score.")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sets_dir")
    ap.add_argument("results", nargs="*")
    ap.add_argument("--min-items", type=int, default=25)
    ap.add_argument("--resamples", type=int, default=5000)
    ap.add_argument("--out", default=None)
    ap.add_argument("--models", nargs="*", default=None)
    ap.add_argument("--heldout", nargs="*", default=[])
    a = ap.parse_args(argv)
    cases = cm.load_cases(a.sets_dir)
    tags = tag_map(cases)
    cov = coverage(cases, tags)
    scores = None
    if a.results:
        tune, _held = cm.split_heldout(cm.load_dirs(a.results, cases, models=a.models), cases, a.heldout)
        scores = pillar_scores(tune, tags, a.resamples)
    print(render_md(cov, scores, a.min_items))
    cm.write_out(a.out, {"coverage": cov, "scores": scores})
    return 0


if __name__ == "__main__":
    sys.exit(main())
