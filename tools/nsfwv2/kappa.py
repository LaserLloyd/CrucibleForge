"""Judge agreement (REBALANCE-PLAN M3).

    python -m tools.nsfwv2.kappa DIR_A DIR_B [--sample-list FILE] [--min-pairs 40] [--out kappa.json]

Cohen's kappa per element between two judge result dirs that judged the SAME
transcripts (paired by model + case + repeat, then element id; the element's
final ``pass``), pooled kappa per case and per pack, and Fleiss' kappa across
the judge samples inside DIR_A (and DIR_B) where per-sample votes exist
(``verified_samples`` over ``judge.samples``). DIR_B may hold only a subset:
only paired decisions count. ``--sample-list``: lines ``case`` or ``case|repeat``
(``#`` comments) restricting the rows compared.
Bands: kappa < 0.4 low, 0.4-0.6 mid, >= 0.6 ok. Kappa is undefined when both
judges are constant on an element (reported as None with the agreement share).
Elements with fewer than --min-pairs paired decisions are not rated singly
(their decisions still feed the case / pack pools).
"""
from __future__ import annotations

import argparse
import sys

from . import _common as cm


def band(k):
    if k is None:
        return "undefined"
    return "low" if k < 0.4 else ("mid" if k < 0.6 else "ok")


def cohen(pairs):
    """pairs: [(a, b)] of 0/1 -> (kappa|None, agreement, n)."""
    n = len(pairs)
    if not n:
        return None, None, 0
    po = sum(1 for a, b in pairs if a == b) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    k = None if pe >= 1.0 else (po - pe) / (1 - pe)
    return k, po, n


def ac1_pabak(pairs):
    """(Gwet AC1, PABAK) for 0/1 pairs: both stay defined and stable when the
    prevalence is extreme (the kappa paradox at p ~ 0.88)."""
    n = len(pairs)
    if not n:
        return None, None
    po = sum(1 for a, b in pairs if a == b) / n
    pi = (sum(a for a, _ in pairs) + sum(b for _, b in pairs)) / (2 * n)
    pe = 2 * pi * (1 - pi)
    ac1 = 1.0 if pe >= 1.0 else (po - pe) / (1 - pe)
    return ac1, 2 * po - 1


def _stats(pr):
    k, po, n = cohen(pr)
    ac1, pabak = ac1_pabak(pr)
    return {"kappa": _round(k), "ac1": _round(ac1), "pabak": _round(pabak), "agree": _round(po), "n": n}


def fleiss(subjects):
    """subjects: [[0/1 votes]] each with the same rater count n>=2 -> (kappa|None, N)."""
    subs = [s for s in subjects if len(s) >= 2]
    if not subs:
        return None, 0
    n = max(set(len(s) for s in subs), key=[len(s) for s in subs].count)
    subs = [s for s in subs if len(s) == n]
    N = len(subs)
    p1 = sum(sum(s) for s in subs) / (N * n)
    pi = [(sum(s) * (sum(s) - 1) + (n - sum(s)) * (n - sum(s) - 1)) / (n * (n - 1)) for s in subs]
    pbar = sum(pi) / N
    pe = p1 ** 2 + (1 - p1) ** 2
    return (None if pe >= 1.0 else (pbar - pe) / (1 - pe)), N


def _round(x):
    return None if x is None else round(x, 3)


def _index(data):
    """{(model, pair-key): row}; the full key (with bench id) wins ties."""
    return {(m, r["pair"]): r for m, rows in data.items() for r in rows}


def _load_sample_list(path):
    keep = set()
    for line in open(path):
        line = line.split("#")[0].strip()
        if line:
            keep.add(line)
    return keep


def compare(data_a: dict, data_b: dict, sample=None, min_pairs=40) -> dict:
    ia, ib = _index(data_a), _index(data_b)
    el = {}       # (case, element) -> [(a, b)]
    n_rows = 0
    for k in sorted(set(ia) & set(ib)):
        case = k[1].split("|")[0]
        if sample and case not in sample and k[1] not in sample:
            continue
        ra, rb = ia[k], ib[k]
        n_rows += 1
        for eid in set(ra["items"]) & set(rb["items"]):
            if eid.startswith("chk:"):
                continue                      # objective checks are the same code in both dirs
            el.setdefault((case, eid), []).append((int(ra["items"][eid]["pass"]), int(rb["items"][eid]["pass"])))
    per_el, per_case, per_pack = {}, {}, {}
    case_pairs, pack_pairs = {}, {}
    for (case, eid), pr in sorted(el.items()):
        k, po, n = cohen(pr)
        rated = n >= min_pairs
        per_el.setdefault(case, {})[eid] = {**_stats(pr), "band": band(k) if rated else "insufficient"}
        case_pairs.setdefault(case, []).extend(pr)
        pack_pairs.setdefault(cm.pack_of(case, None) or "?", []).extend(pr)
    for case, pr in case_pairs.items():
        k = cohen(pr)[0]
        per_case[case] = {**_stats(pr), "band": band(k)}
    for pk, pr in pack_pairs.items():
        k = cohen(pr)[0]
        per_pack[pk] = {**_stats(pr), "band": band(k)}
    all_pairs = [x for pr in case_pairs.values() for x in pr]
    k, po, n = cohen(all_pairs)

    def fl(data):
        subj = {}
        for m, rows in data.items():
            for r in rows:
                for eid, it in r["items"].items():
                    if it["yes"]:
                        subj.setdefault((r["case"], eid), []).append(it["yes"])
        out, allsub = {}, []
        for (case, eid), s in subj.items():
            k2, N = fleiss(s)
            out.setdefault(case, {})[eid] = {"kappa": _round(k2), "n_rows": N}
            allsub.extend(s)
        k3, N3 = fleiss(allsub)
        return {"elements": out, "pooled": {"kappa": _round(k3), "n_rows": N3}}
    rated = [(c, e, v) for c, d in per_el.items() for e, v in d.items() if v["band"] != "insufficient"]
    return {"paired_rows": n_rows, "paired_decisions": n, "pooled": {**_stats(all_pairs), "band": band(k)}, "per_pack": per_pack, "per_case": per_case, "per_element": per_el,
            "rated_elements": len(rated),
            "rated_ok_share": None if not rated else round(sum(1 for _, _, v in rated if v["band"] == "ok"
                                                              or (v["kappa"] is None and v["agree"] == 1.0)) / len(rated), 3),
            "elements_total": len(el), "min_pairs": min_pairs,
            "fleiss_a": fl(data_a), "fleiss_b": fl(data_b)}


def render_md(r: dict) -> str:
    L = ["# Judge agreement", "",
         f"Paired rows {r['paired_rows']}, paired decisions {r['paired_decisions']}; pooled Cohen kappa "
         f"{r['pooled']['kappa']} ({r['pooled']['band']}), Gwet AC1 {r['pooled']['ac1']}, PABAK {r['pooled']['pabak']}, "
         f"raw agreement {r['pooled']['agree']}.",
         f"Elements rateable singly (>= {r['min_pairs']} pairs): {r['rated_elements']} of {r['elements_total']}"
         + ("" if r['rated_ok_share'] is None else f"; kappa >= 0.6 on {r['rated_ok_share']:.0%} of them."),
         f"Fleiss across samples, pooled: A {r['fleiss_a']['pooled']['kappa']} (n={r['fleiss_a']['pooled']['n_rows']}), "
         f"B {r['fleiss_b']['pooled']['kappa']} (n={r['fleiss_b']['pooled']['n_rows']}).", "",
         "| pack | kappa | AC1 | PABAK | agree | n | band |", "|---|---|---|---|---|---|---|"]
    for p, v in sorted(r["per_pack"].items()):
        L.append(f"| {p} | {v['kappa']} | {v['ac1']} | {v['pabak']} | {v['agree']} | {v['n']} | {v['band']} |")
    L += ["", "| case | kappa | AC1 | PABAK | agree | n | band | elements low/mid/ok/insufficient |", "|---|---|---|---|---|---|---|---|"]
    for c, v in sorted(r["per_case"].items()):
        cnt = {b: 0 for b in ("low", "mid", "ok", "undefined", "insufficient")}
        for e in r["per_element"][c].values():
            cnt[e["band"]] += 1
        L.append(f"| {c} | {v['kappa']} | {v['ac1']} | {v['pabak']} | {v['agree']} | {v['n']} | {v['band']} | "
                 f"{cnt['low']}/{cnt['mid']}/{cnt['ok']}/{cnt['insufficient']} |")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir_a")
    ap.add_argument("dir_b")
    ap.add_argument("--sample-list", default=None)
    ap.add_argument("--min-pairs", type=int, default=40)
    cm.add_common_args(ap)
    a = ap.parse_args(argv)
    cases = cm.load_cases(a.sets_dir)
    da = cm.load_dirs([a.dir_a], cases, models=a.models)
    db = cm.load_dirs([a.dir_b], cases, models=a.models)
    sample = _load_sample_list(a.sample_list) if a.sample_list else None
    res = compare(da, db, sample, a.min_pairs)
    print(render_md(res))
    cm.write_out(a.out, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
