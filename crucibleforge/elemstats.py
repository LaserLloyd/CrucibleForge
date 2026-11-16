"""Element table of a finished run (4.5 Main tuning; chains.py flat elements).

    uv run python -m crucibleforge.elemstats MODEL [MODEL2 ...] [--results DIR] [--json]

For each model's stored rows (``results/transcripts_<label>.jsonl``), per family x level:
n elements, mean score, sd, tokens per element (thinking / answer; a prompt's tokens are
split evenly over its elements), share "ran out", "context" (a session's context was
exhausted) and "missing", the within-unit ICC (unit = multi-item prompt, or per-turn item),
prompt tokens per unit (all its requests), cached prompt tokens per unit and the cache-hit
share (cached / prompt over the requests that report their cache reuse) and, for every pair of models, the paired resolving
power per token = 1 / (sd_d^2 x tokens per element), d = the per-element score difference
over the elements both models answered (paired by seed: element ids are the same for every
model) and tokens = the mean of the two models' tokens per element. Read-only.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

from . import chains, config


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def elements(rows: list[dict], rules: dict | None = None) -> dict[str, dict]:
    """{element id: {stratum, unit, score, outcome, think, answer}} from a model's chain rows
    (newest graded attempt of each instance). Tokens are the unit's (all its attempt rows
    for that instance) split evenly over its elements. ``rules``: {cell: ran-out rule} of the
    scoring profile (scale.ran_out_rules); a cell it does not name keeps the stored block's."""
    from . import scale
    crow = [r for r in rows if r.get("chain")]
    blocks = chains._newest_blocks(crow)
    tok: dict[tuple, list[float]] = {}
    # 4.5 empty_stop_rule: turns of the unit stamped empty_stop (finish=stop, no visible content)
    estop: dict[tuple, int] = {}
    # degenerate replies (degenerate.py; a loop counts as a failure, lead 2026-11-14)
    dgen: dict[tuple, int] = {}
    from . import degenerate as _dg
    for r in crow:
        ch = r["chain"]
        k = (ch["id"], int(ch.get("rung") or 1), int(ch.get("attempt") or 1))
        if r.get("empty_stop"):
            estop[k] = estop.get(k, 0) + 1
        if _dg.is_degenerate(r):
            dgen[k] = dgen.get(k, 0) + 1
        m = r.get("metrics") or {}
        think = _num(m.get("reasoning_tokens"))
        ans = m.get("answer_tokens")
        ans = _num(ans) if ans is not None else max(0.0, _num(m.get("completion_tokens")) - think)
        t = tok.setdefault(k, [0.0, 0.0, 0.0, 0.0, 0.0])
        t[0] += think
        t[1] += ans
        pt = _num(m.get("prompt_tokens"))
        t[2] += pt
        if m.get("cache_read_tokens") is not None:
            # cached prompt tokens (llama-server / StudioForge report the cache reuse of each
            # request); the hit share is taken over the requests that report it
            t[3] += _num(m.get("cache_read_tokens"))
            t[4] += pt
    out: dict[str, dict] = {}
    for k, blk in blocks.items():
        kind = blk.get("kind", "chain")
        if kind == "chain":
            stratum = f"chain:{blk.get('ladder')} L{blk.get('level')}"
        else:
            fam = blk.get("family") + (f"-{blk['variant']}" if blk.get("variant") else "")
            stratum = f"{fam} L{blk.get('level')}" + (" /turn" if blk.get("element") == "turn" else "")
        els = blk.get("elements") or [{"id": blk["id"] if k[1:] == (1, 1) else f"{blk['id']}-r{k[1]}-a{k[2]}",
                                      "score": blk.get("score"), "outcome": blk.get("outcome")}]
        # a weight-0 element (contested / record-only check) earns nothing: not in the statistics
        els = [e for e in els if chains._weight_of(e) > 0] or els[:0]
        if not els:
            continue
        th, an, pt, cr, known = tok.get(k, [0.0] * 5)
        n = max(1, len(els))
        for e in els:
            out[e["id"]] = {"stratum": stratum, "unit": blk["id"], "score": float(e.get("score") or 0.0),
                            "outcome": e.get("outcome"), "think": th / n, "answer": an / n,
                            "cell": stratum, "concept": blk.get("concept") or "-", "band": blk.get("band") or "-",
                            # the scoring profile's rule for the cell (scale.ran_out_rules) when given,
                            # else the stored block's own
                            "rule": (rules or {}).get(scale.cell_of(blk)) or blk.get("ran_out") or "zero",
                            # the unit's own totals (not split): prompt tokens over all its
                            # requests, cached prompt tokens, prompt tokens of the requests
                            # that reported their cache
                            "unit_prompt": pt, "unit_cached": cr, "unit_cache_known": known,
                            "unit_empty_stop": estop.get(k, 0),
                            "unit_degenerate": dgen.get(k, 0)}
    return out


def _sd(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def regroup(per_model: dict[str, dict[str, dict]], by: str = "cell") -> dict[str, dict[str, dict]]:
    """The same elements with ``stratum`` = their cell (default), ``concept``, ``band`` or
    ``concept x band`` (bank tags; "-" = untagged)."""
    if by == "cell":
        return per_model
    key = {"concept": lambda e: e["concept"], "band": lambda e: e["band"],
           "concept-band": lambda e: f"{e['concept']} {e['band']}"}[by]
    return {m: {i: {**e, "stratum": key(e)} for i, e in els.items()} for m, els in per_model.items()}


def table(per_model: dict[str, dict[str, dict]], by: str = "cell") -> list[dict]:
    """One row per (model, stratum), plus one per (model pair, stratum) for resolving power.
    ``by``: cell (family x level), concept, band or concept-band."""
    per_model = regroup(per_model, by)
    rows = []
    strata = sorted({e["stratum"] for els in per_model.values() for e in els.values()})
    for m, els in per_model.items():
        for s in strata:
            es_all = [e for e in els.values() if e["stratum"] == s]
            if not es_all:
                continue
            # `ran_out: exclude` cells: a "ran out" element is reported (share below), not scored
            es = [e for e in es_all if not (e.get("rule") == "exclude" and e["outcome"] == "ran out")] or es_all[:0]
            if not es:
                rows.append({"model": m, "stratum": s, "n": 0, "units": 0, "mean": None, "sd": None,
                             "think": 0.0, "answer": 0.0, "ran_out": 1.0, "context": 0.0, "missing": 0.0,
                             "icc": None, "prompt_unit": 0.0, "cached_unit": None, "cache_hit": None,
                             "ceiling_too_low": True, "excluded": len(es_all),
                             "empty_stop": sum({e["unit"]: int(e.get("unit_empty_stop") or 0)
                                                for e in es_all}.values()),
                             "degenerate": sum({e["unit"]: int(e.get("unit_degenerate") or 0)
                                                for e in es_all}.values())})
                continue
            xs = [e["score"] for e in es]
            units: dict[str, list[float]] = {}
            for e in es:
                units.setdefault(e["unit"], []).append(e["score"])
            n = len(es)
            per_unit = {e["unit"]: e for e in es}.values()
            known = sum(_num(e.get("unit_cache_known")) for e in per_unit)
            cached = sum(_num(e.get("unit_cached")) for e in per_unit)
            rows.append({"model": m, "stratum": s, "n": n, "units": len(units),
                         "prompt_unit": sum(_num(e.get("unit_prompt")) for e in per_unit) / len(units),
                         "cached_unit": (cached / len(units)) if known else None,
                         "cache_hit": (cached / known) if known else None,
                         "mean": sum(xs) / n, "sd": _sd(xs),
                         "think": sum(e["think"] for e in es) / n,
                         "answer": sum(e["answer"] for e in es) / n,
                         "ran_out": sum(e["outcome"] == "ran out" for e in es_all) / len(es_all),
                         "ceiling_too_low": sum(e["outcome"] == "ran out" for e in es_all) / len(es_all)
                         > chains.RAN_OUT_FLAG,
                         "excluded": len(es_all) - n,
                         "context": sum(e["outcome"] == "context" for e in es) / n,
                         "missing": sum(e["outcome"] == "missing" for e in es) / n,
                         # turns stamped empty_stop in this set's units (a count, not a share)
                         "empty_stop": sum({e["unit"]: int(e.get("unit_empty_stop") or 0)
                                            for e in es_all}.values()),
                         # degenerate replies in this set's units (counted as failures; a count)
                         "degenerate": sum({e["unit"]: int(e.get("unit_degenerate") or 0)
                                            for e in es_all}.values()),
                         "icc": chains.icc_anova(list(units.values()))})
    for a, b in itertools.combinations(per_model, 2):
        A, B = per_model[a], per_model[b]
        for s in strata:
            ids = sorted(i for i in set(A) & set(B) if A[i]["stratum"] == s)
            if len(ids) < 2:
                continue
            d = [A[i]["score"] - B[i]["score"] for i in ids]
            sdd = _sd(d)
            tokens = sum(A[i]["think"] + A[i]["answer"] + B[i]["think"] + B[i]["answer"] for i in ids) / (2 * len(ids))
            rp = (1.0 / (sdd ** 2 * tokens)) if sdd and tokens else None
            rows.append({"pair": f"{a} - {b}", "stratum": s, "n": len(ids), "mean_d": sum(d) / len(d),
                         "sd_d": sdd, "tokens": tokens, "rp_per_token": rp})
    return rows


def briefs(rows: list[dict]) -> list[dict]:
    """One record per seeded instance ("brief") of a flat set: its set, whether it was
    packed, its score (the weighted share of its elements' stored weights, i.e. the score
    the family is screened on), its tokens (a packed prompt's split evenly over its briefs)
    and whether it ran out. The key is the instance's generator key, so a set aliased by
    ``seed_as`` pairs brief for brief with its target."""
    crow = [r for r in rows if r.get("chain")]
    blocks = chains._newest_blocks(crow)
    tok: dict[tuple, float] = {}
    for r in crow:
        ch = r["chain"]
        k = (ch["id"], int(ch.get("rung") or 1), int(ch.get("attempt") or 1))
        tok[k] = tok.get(k, 0.0) + _num((r.get("metrics") or {}).get("completion_tokens"))
    out = []
    for k, blk in blocks.items():
        if blk.get("kind") not in ("flat", "multi") or blk.get("score") is None:
            continue
        els = blk.get("elements") or []
        if blk["kind"] == "multi":
            keys = list(blk.get("items") or [])
            groups = {i: [e for e in els if e.get("k") == i] for i in range(1, len(keys) + 1)}
        else:
            keys = [blk.get("seed_id")]
            groups = {1: els}
        n = max(1, len(keys))
        for i, key in enumerate(keys, 1):
            es = groups.get(i) or []
            w = sum(chains._weight_of(e) for e in es)
            share = (sum(chains._weight_of(e) * float(e.get("score") or 0.0) for e in es) / w if w
                     else float(blk.get("score") or 0.0))
            out.append({"set": blk.get("ladder"), "packed": blk["kind"] == "multi", "key": key,
                        "stratum": f"{blk.get('family')} L{blk.get('level')}", "share": share,
                        "tokens": tok.get(k, 0.0) / n,
                        "ran_out": any(e.get("outcome") == "ran out" for e in es)
                        or (blk["kind"] == "flat" and bool(blk.get("cut_off")))})
    return out


def packing(per_model: dict[str, list[dict]]) -> list[dict]:
    """Single vs packed on the SAME briefs (pairs a packed set with a single set that shares
    its generator keys, e.g. via ``seed_as``): per model and pair of sets, n paired briefs,
    mean score of each arm, mean and sd of (packed - single), tokens per brief of each arm,
    ran-out counts."""
    rows = []
    for m, bs in per_model.items():
        single = {(b["stratum"], b["key"]): b for b in bs if not b["packed"]}
        pairs: dict[tuple, list] = {}
        for b in bs:
            if b["packed"] and (b["stratum"], b["key"]) in single:
                s = single[(b["stratum"], b["key"])]
                pairs.setdefault((b["stratum"], s["set"], b["set"]), []).append((s, b))
        for (st, sset, pset), ps in sorted(pairs.items()):
            d = [p["share"] - s["share"] for s, p in ps]
            n = len(ps)
            rows.append({"model": m, "stratum": st, "single": sset, "packed": pset, "n": n,
                         "mean_single": sum(s["share"] for s, _ in ps) / n,
                         "mean_packed": sum(p["share"] for _, p in ps) / n,
                         "mean_d": sum(d) / n, "sd_d": _sd(d),
                         "tok_single": sum(s["tokens"] for s, _ in ps) / n,
                         "tok_packed": sum(p["tokens"] for _, p in ps) / n,
                         "ran_out_single": sum(s["ran_out"] for s, _ in ps),
                         "ran_out_packed": sum(p["ran_out"] for _, p in ps)})
    return rows


def render_packing(rows: list[dict]) -> str:
    def f(v, p=2):
        return "-" if v is None else f"{v:.{p}f}"
    L = ["| model | family x level | single set | packed set | n paired briefs | single | packed | mean d (packed - single) "
         "| sd_d | tokens/brief single | tokens/brief packed | ran out single | ran out packed |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['model']} | {r['stratum']} | {r['single']} | {r['packed']} | {r['n']} | {f(r['mean_single'])} | "
                 f"{f(r['mean_packed'])} | {f(r['mean_d'])} | {f(r['sd_d'])} | {r['tok_single']:.0f} | "
                 f"{r['tok_packed']:.0f} | {r['ran_out_single']} | {r['ran_out_packed']} |")
    return "\n".join(L)


def render(rows: list[dict]) -> str:
    def f(v, p=2):
        return "-" if v is None else f"{v:.{p}f}"
    def pct(v):
        return "-" if v is None else f"{v:.0%}"
    L = ["| model | group | n | units | mean | sd | think/el | answer/el | ran out | context | missing | ICC "
         "| prompt/unit | cached/unit | cache hit | degenerate | empty stop |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "model" in r:
            ro = f"{r['ran_out']:.0%}"
            if r.get("ceiling_too_low"):
                ro += " (ceiling too low)"
            if r.get("excluded"):
                ro += f" ({r['excluded']} excluded)"
            L.append(f"| {r['model']} | {r['stratum']} | {r['n']} | {r['units']} | {f(r['mean'])} | {f(r['sd'])} | "
                     f"{r['think']:.0f} | {r['answer']:.0f} | {ro} | {r['context']:.0%} | "
                     f"{r['missing']:.0%} | {f(r['icc'])} | {r['prompt_unit']:.0f} | "
                     f"{f(r['cached_unit'], 0)} | "
                     f"{pct(r['cache_hit'])} | {r.get('degenerate', 0)} | {r.get('empty_stop', 0)} |")
    pairs = [r for r in rows if "pair" in r]
    if pairs:
        L += ["", "| pair | family x level | n paired | mean d | sd_d | tokens/el | 1/(sd_d^2 x tokens) x 1e6 |",
              "|---|---|---|---|---|---|---|"]
        for r in pairs:
            rp = None if r["rp_per_token"] is None else r["rp_per_token"] * 1e6
            L.append(f"| {r['pair']} | {r['stratum']} | {r['n']} | {f(r['mean_d'])} | {f(r['sd_d'])} | "
                     f"{r['tokens']:.0f} | {f(rp)} |")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m crucibleforge.elemstats", description=__doc__.split("\n\n")[0])
    ap.add_argument("models", nargs="+", help="model labels (results/transcripts_<label>.jsonl)")
    ap.add_argument("--results", help="results directory (default: the configured one)")
    ap.add_argument("--json", action="store_true", help="print the rows as JSON")
    ap.add_argument("--by", choices=("cell", "concept", "band", "concept-band"), default="cell",
                    help="group elements by cell (family x level, default) or by bank tag")
    ap.add_argument("--packing", action="store_true",
                    help="single vs packed on the same briefs (sets paired by generator key, e.g. seed_as)")
    a = ap.parse_args(argv)
    if a.results:
        config.RESULTS_DIR = Path(a.results)
    if a.packing:
        rows_p = packing({m: briefs(config.load_transcripts(m)) for m in a.models})
        if not rows_p:
            print("no packed set shares briefs with a single set (use seed_as)", file=sys.stderr)
            return 1
        print(json.dumps(rows_p, indent=1) if a.json else render_packing(rows_p))
        return 0
    per = {}
    for m in a.models:
        rows = config.load_transcripts(m)
        per[m] = elements(rows)
        if not per[m]:
            print(f"{m}: no element rows (chains / flat sets) in {config.transcript_path(m)}", file=sys.stderr)
    out = table(per, a.by)
    print(json.dumps(out, indent=1) if a.json else render(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
