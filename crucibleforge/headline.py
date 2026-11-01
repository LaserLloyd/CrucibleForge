"""The board headline (suite 3.9): a rough, exponential score with a margin
of error, and tie groups — "is model A better than model B?" (the maintainer
2026-10-28: "I really just want to know which model is better than another").

Points
------
Each category (profile ``scores:`` — Coding/Work, Chat, NSFW) is out of 10
points and is the sum of its sub-scores' points (share x max, whole-number
maxima). A category's raw ``P`` (0-100 on the standard test, + 0.5 x a
bonus pack's share above it) maps to the headline ``S``:

- ``P <= 100``: ``S = P / 10``           (93 % -> 9.3; the standard test tops out at 10)
- ``P >  100``: ``S = 10 + 2 * (1 - 2 ** (-(P - 100) / 50))``
  (125 % -> 10.6, 150 % -> 11.0, 200 % -> 11.5, asymptote 12): every step
  above 10 costs exponentially more bonus points.

Overall (profile ``headline.overall``): ``sum`` (the maintainer 2026-10-28) = the sum
of the in-total categories' S (Coding/Work + Chat: max 20, 24 with packs);
``mean`` = S of the mean P (the earlier rule). NSFW (``in_total: false``) is
never added.

Margin of error
---------------
A two-level stratified bootstrap: the case ids of every stratum (each
sub-score's declared cases, then each (tier, category)) are resampled with
replacement, and inside every drawn case its repeats are resampled with
replacement too (generation noise, not only item sampling; EVAL-FABLE S7).
Multi-turn rows travel with their repeat. The model's rows are rebuilt from
the draw and the FULL scoring runs again (component weights, the pack gates
recomputed on the resampled standard score, the 0.5 pack weight), then S /
points are taken. ``±m`` = half the LEVEL (90 %) interval, rounded to 0.1,
at least 0.1. Fixed seed: the same rows give the same board.

Who is better
-------------
Paired: the same draws (cases AND repeat slots) are applied to both models,
so ``S_A - S_B`` per resample is a paired difference. A model is TIED with
the one ranked directly above it when that difference's interval contains
0. Rank labels "1", "2=", "2="...: a tie group is a chain of ADJACENT ties
(A≈B and B≈C put A, B, C in one group even when A beats C).
"""
from __future__ import annotations

import math
import random

#: bootstrap resamples per model (>= 1000) and the fixed seed
RESAMPLES = 1000
SEED = 20261028
#: two-sided interval used for ±m and for tie decisions (default; the
#: profile's ``headline.level`` is passed explicitly)
LEVEL = 0.90
#: a gate is "near" when between these shares of the resamples pass it
NEAR_GATE = (0.05, 0.95)
#: one point above 100 % halves the remaining headroom every 50 points
BONUS_HALF_LIFE = 50.0
BONUS_SPAN = 2.0

#: the legacy score keys (a profile's ``scores:`` block replaces them:
#: tiers.score_keys)
AREAS = ("code", "reason", "chat")


#: 4.0 capability classes (the maintainer 2026-10-31: "general categories based on how
#: capable they are"): the first band whose floor Overall (/20, sum mode)
#: reaches. Set from the 4.0 board (REPORT.md); profile ``headline.classes``
#: overrides. A model with no Overall (partial) has no class.
CLASSES = {"S": 16.0, "A": 13.0, "B": 10.0, "C": 0.0}
#: LEAN: (Chat - board median Chat) - (Coding - board median Coding) at least
#: this far from 0 (Fable concept review 2026-10-31)
LEAN_GAP = 1.5


def capability_class(overall: float | None, prof: dict | None = None) -> str | None:
    if overall is None:
        return None
    bands = ((prof or {}).get("headline") or {}).get("classes") or CLASSES
    for name, floor in sorted(bands.items(), key=lambda kv: -float(kv[1])):
        if overall >= float(floor):
            return str(name)
    return None


def board_medians(pairs: list[tuple]) -> tuple[float, float] | None:
    """(median Chat, median Coding) over the models with both measured."""
    import statistics
    both = [(a, b) for a, b in pairs if a is not None and b is not None]
    if not both:
        return None
    return (statistics.median(a for a, _ in both), statistics.median(b for _, b in both))


def lean(chat: float | None, code: float | None, prof: dict | None = None,
         medians: tuple[float, float] | None = None) -> str | None:
    """"chat-leaning" / "code-leaning" when (Chat - median) - (Coding - median)
    is at least the gap from 0, else "balanced"; None when either is
    unmeasured. ``medians`` = (median Chat, median Coding) of the board
    (None: no centring)."""
    if chat is None or code is None:
        return None
    gap = float(((prof or {}).get("headline") or {}).get("lean_gap", LEAN_GAP))
    mc, mk = medians or (0.0, 0.0)
    d = (chat - mc) - (code - mk)
    if d >= gap:
        return "chat-leaning"
    if -d >= gap:
        return "code-leaning"
    return "balanced"


def areas(prof: dict | None) -> tuple[str, ...]:
    from . import tiers as _tiers
    return tuple(_tiers.score_keys(prof))


def part_keys(prof: dict | None) -> tuple[str, ...]:
    from . import tiers as _tiers
    return tuple(f"{d['key']}/{p['key']}" for d in _tiers.scores(prof)
                 for p in d.get("parts") or [])


def keys(prof: dict | None) -> tuple[str, ...]:
    """Every headline key: the categories, then each sub-score "<cat>/<part>"."""
    return areas(prof) + part_keys(prof)


def part_max(prof: dict | None) -> dict[str, float]:
    from . import tiers as _tiers
    return {f"{d['key']}/{p['key']}": p["weight"] for d in _tiers.scores(prof)
            for p in d.get("parts") or []}


def overall_mode(prof: dict | None) -> str:
    return str(((prof or {}).get("headline") or {}).get("overall") or "mean")


def scale(p: float | None) -> float | None:
    """Raw percent (0-200) -> headline S (0-12, unrounded)."""
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return None
    p = max(0.0, float(p))
    if p <= 100.0:
        return p / 10.0
    return 10.0 + BONUS_SPAN * (1.0 - 2.0 ** (-(p - 100.0) / BONUS_HALF_LIFE))


def unscale(s: float) -> float:
    """Inverse of :func:`scale` (S < 12)."""
    if s <= 10.0:
        return s * 10.0
    return 100.0 - BONUS_HALF_LIFE * math.log2(1.0 - (s - 10.0) / BONUS_SPAN)


def quantile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated quantile of an already sorted list."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    i = int(math.floor(pos))
    j = min(i + 1, n - 1)
    return sorted_vals[i] + (sorted_vals[j] - sorted_vals[i]) * (pos - i)


def interval(samples: list[float], level: float = LEVEL) -> tuple[float | None, float | None]:
    vals = sorted(v for v in samples if v is not None)
    if not vals:
        return None, None
    a = (1.0 - level) / 2.0
    return quantile(vals, a), quantile(vals, 1.0 - a)


def margin(samples: list[float], level: float = LEVEL) -> float | None:
    """±m: half the ``level`` interval of the samples, rounded to 0.1, >= 0.1."""
    lo, hi = interval(samples, level)
    if lo is None:
        return None
    return max(0.1, round((hi - lo) / 2.0, 1))


def fmt_s(s: float | None, m: float | None = None, html: bool = False) -> str:
    """"**9**.3 ± 0.4" (markdown) or "<b>9</b>.3 ± 0.4" (html): the integer
    part is the main number."""
    if s is None:
        return "-"
    txt = f"{s:.1f}"
    whole, dec = txt.split(".")
    head = f"<b class=\"s-main\">{whole}</b>.{dec}" if html else f"**{whole}**.{dec}"
    if m is None:
        return head
    return f"{head} ± {m:.1f}"


# ----------------------------------------------------------- strata / draws

def strata(prof: dict, sc: dict, allowed: set[str] | None) -> dict[tuple[int, str], list[str]]:
    """{(tier, stratum): sorted case ids} from the PROFILE (so every model
    gets the same draws). Declared sub-score case lists first (a sub-score
    never loses all its cases in a resample), then every other tier-1 case
    by the category its weights feed, then the packs per (tier, category);
    perf (unscored) is left out. ``allowed`` = the board's case ids
    (hidden/private ones already removed on a public board)."""
    from . import report as _r
    from . import tiers as _tiers
    out: dict[tuple[int, str], list[str]] = {}
    w1 = _tiers.tier_scoring(sc, prof, 1)
    placed: set[str] = set()
    declared = _r.declared_case_sets(prof, None) if prof.get("scores") else {}
    for d in _tiers.scores(prof):
        for name in [f"{d['key']}/{p['key']}" for p in d.get("parts") or []] + [d["key"]]:
            for i in sorted(declared.get(name) or ()):
                if i in placed or (allowed is not None and i not in allowed):
                    continue
                placed.add(i)
                out.setdefault((1, name), []).append(i)
    for cat, ids in (prof.get("cases") or {}).items():
        wk = _tiers.weight_keys([cat])
        for area in areas(prof):
            if wk & set(w1.get(_tiers.group_of(prof, area)) or {}):
                out.setdefault((1, area), []).extend(
                    i for i in ids or [] if (allowed is None or i in allowed) and i not in placed)
                break
    if prof.get("scores"):
        # the points board: every ladder set is a stratum of its own
        for key, seq in _r._ladder_sets(prof).items():
            for st_ in seq:
                out.setdefault((1 + st_["n"], key), []).extend(
                    i for i in st_["cases"] if allowed is None or i in allowed)
    elif _tiers.enabled(prof):
        for tier in _tiers.TIERS:
            for area, cats in _tiers.tier_lists(prof, tier).items():
                for ids in cats.values():
                    out.setdefault((tier, area), []).extend(
                        i for i in ids if allowed is None or i in allowed)
    return {k: sorted(set(v)) for k, v in out.items() if v}


def draws(st: dict[tuple[int, str], list[str]], n: int = RESAMPLES,
          seed: int = SEED) -> list[dict[str, list[int]]]:
    """``n`` resamples: each is {case id: [one repeat-draw seed per copy]}
    over every stratum (a stratum of k cases draws k ids with replacement;
    each drawn copy carries a seed that picks its repeats)."""
    rnd = random.Random(seed)
    out = []
    ks = sorted(st)
    for _ in range(n):
        draw: dict[str, list[int]] = {}
        for k in ks:
            ids = st[k]
            for _ in ids:
                cid = ids[rnd.randrange(len(ids))]
                draw.setdefault(cid, []).append(rnd.getrandbits(32))
        out.append(draw)
    return out


def identity_draw(st: dict[tuple[int, str], list[str]]) -> dict[str, list[None]]:
    """Every case once, its repeats as observed (the point estimate)."""
    return {cid: [None] for ids in st.values() for cid in ids}


def resample_rows(rows: list[dict], draw: dict, stratified: set[str],
                  two_level: bool = True) -> list[dict]:
    """The rows of one resample. A case drawn k times contributes k copies
    (copy j tagged ``_boot_k = j``, so per-case aggregates — code bank
    tasks, deep-RP cases — count it k times). With ``two_level`` each copy
    also resamples its repeats with replacement from its seed: repeat slot
    i takes the rows of a random observed repeat (tagged ``_boot_r = i``,
    the original ``repeat`` kept so a cached per-row score stays valid; a
    session's turns travel with their repeat). A copy seed of None keeps the
    observed repeats. Rows of cases outside every stratum (perf) pass
    through unchanged. ``draw`` values may also be plain counts (int)."""
    by_case: dict[str, list[dict]] = {}
    out = []
    for r in rows:
        cid = r.get("case_id")
        if cid not in stratified:
            out.append(r)
        else:
            by_case.setdefault(cid, []).append(r)
    for cid, crow in by_case.items():
        copies = draw.get(cid, 0)
        if isinstance(copies, int):
            copies = [None] * copies
        reps = sorted({r.get("repeat") for r in crow}, key=lambda x: (x is None, x or 0))
        by_rep: dict = {}
        for r in crow:
            by_rep.setdefault(r.get("repeat"), []).append(r)
        for j, cseed in enumerate(copies):
            if cseed is None or not two_level or len(reps) < 2:
                chosen = list(enumerate(reps))
                resampled = False
            else:
                rr = random.Random(cseed)
                chosen = [(i, reps[rr.randrange(len(reps))]) for i in range(len(reps))]
                resampled = True
            for i, rep in chosen:
                for r in by_rep[rep]:
                    if j == 0 and not resampled:
                        out.append(r)
                        continue
                    nr = dict(r)
                    if j:
                        nr["_boot_k"] = j
                    if resampled:
                        nr["_boot_r"] = i
                    out.append(nr)
    return out


# ----------------------------------------------------------- per model

def point(card: dict, prof: dict | None = None) -> dict:
    """Raw values from a (tier) scorecard: {category: P (0-200), "<cat>/<part>":
    P of the sub-score's standard share (0-100), "overall": the card total}."""
    from . import tiers as _tiers
    p = {a: card.get(_tiers.group_of(prof, a)) for a in areas(prof)}
    for score, parts in (card.get("parts") or {}).items():
        for pk, v in parts.items():
            p[f"{score}/{pk}"] = v
    p["overall"] = card.get("total")
    p["_units"] = card.get("units")
    return p


def display(p: dict, prof: dict | None) -> dict:
    """Headline values from raw ones: categories -> S, sub-scores -> points
    (share x max), overall -> sum of the in-total S (``overall: sum``) or S
    of the mean P (``mean``)."""
    from . import tiers as _tiers
    if p.get("_units") == "points":
        # the points board: categories and sub-scores ARE points (ladder sets
        # add +1 each; no exponential map), Overall is already the sum
        return {k: p.get(k) for k in keys(prof) + ("overall",)}
    out = {a: scale(p.get(a)) for a in areas(prof)}
    for k, mx in part_max(prof).items():
        v = p.get(k)
        out[k] = None if v is None else v / 100.0 * mx
    if overall_mode(prof) == "sum":
        vals = [out.get(k) for k in _tiers.total_keys(prof)]
        out["overall"] = None if (not vals or any(v is None for v in vals)) else sum(vals)
    else:
        out["overall"] = scale(p.get("overall"))
    return out


def boot_model(rows_by_tier: dict[int, list[dict]], label: str, cfg: dict | None,
               meta: dict | None, era: dict | None, prof: dict, sc: dict,
               st: dict, mults: list[dict], two_level: bool = True) -> dict:
    """Headline samples per key (headline.display) and the pack-gate pass
    counts for one model."""
    from . import report as _r
    stratified = {i for ids in st.values() for i in ids}
    samples = {a: [] for a in keys(prof) + ("overall",)}
    gate_pass: dict[str, int] = {}
    with _r.boot_mode():
        for draw in mults:
            _r.boot_set_mult({cid: (c if isinstance(c, int) else len(c)) for cid, c in draw.items()})
            by_t = {t: resample_rows(rows_by_tier.get(t) or [], draw, stratified, two_level)
                    for t in (1, 2, 3)}
            s = _r.stats_from_tier_rows(label, cfg, prof, by_t, meta, era)
            card = _r.tier_scorecard(s, sc, prof)
            v = display(point(card, prof), prof)
            for a in samples:
                samples[a].append(v.get(a))
            for area, e in (card.get("tiers") or {}).items():
                st2 = (e.get("state") or {}).get(2)
                if st2 in ("scored", "pending", "na_ctx"):
                    gate_pass[area] = gate_pass.get(area, 0) + 1
            for cat, parts in (card.get("ladder") or {}).items():
                for pk, det in parts.items():
                    if det.get("sets") and det["sets"][0]["state"] != "locked":
                        k = f"{cat}/{pk}"
                        gate_pass[k] = gate_pass.get(k, 0) + 1
    return {"samples": samples, "gate_pass": gate_pass, "n": len(mults)}


def summarize(card: dict, boot: dict | None, prof: dict | None,
              level: float = LEVEL) -> dict:
    """The headline block stored in stats[label]["headline"]: raw ``P``,
    ``S`` (categories + overall, one decimal), ``pts`` (sub-score points),
    ``m`` (± on the same scale), ``interval`` and ``near_gate``."""
    from . import tiers as _tiers
    p = point(card, prof)
    v = display(p, prof)
    out = {"P": p, "S": {}, "pts": {}, "max": part_max(prof), "m": {}, "interval": {},
           "near_gate": [], "overall_mode": overall_mode(prof),
           # unrounded overall: the board's sort key
           "sort": v.get("overall"),
           # the standard-test (T1) percent per category, before any pack bonus
           "T1": {a: (card.get("t1") or {}).get(_tiers.group_of(prof, a)) for a in areas(prof)}}
    for a in keys(prof) + ("overall",):
        x = v.get(a)
        (out["pts"] if "/" in a else out["S"])[a] = None if x is None else round(x, 1)
        if boot and x is not None:
            sm = boot["samples"][a]
            out["m"][a] = margin(sm, level)
            lo, hi = interval(sm, level)
            out["interval"][a] = (round(lo, 2), round(hi, 2)) if lo is not None else None
        else:
            out["m"][a] = None
    out["maxed"] = card.get("maxed") or {}
    # 3.10 D3: rows a sub-score dropped (unparsable verdicts) — "partial"
    out["excluded"] = dict(card.get("excluded") or {})
    # categories with standard rows still missing (report.points_scorecard, S3)
    out["partial"] = list(card.get("partial") or [])
    out["reached"] = {f"{cat}/{pk}": det.get("reached", 0)
                      for cat, parts in (card.get("ladder") or {}).items()
                      for pk, det in parts.items()}
    if boot and prof and card.get("units") == "points":
        for cat, parts in (card.get("ladder") or {}).items():
            for pk, det in parts.items():
                if not det.get("sets"):
                    continue
                k = f"{cat}/{pk}"
                share = boot["gate_pass"].get(k, 0) / max(1, boot["n"])
                if NEAR_GATE[0] < share < NEAR_GATE[1]:
                    from . import ladder as _ladder
                    out["near_gate"].append({"area": k, "tier": 1, "pass_share": round(share, 2),
                                             "gate": 100 * _ladder.unlock_share(prof)})
    elif boot and prof:
        for area in areas(prof):
            if area not in _tiers.tier_lists(prof, 2):
                continue
            share = boot["gate_pass"].get(area, 0) / max(1, boot["n"])
            if NEAR_GATE[0] < share < NEAR_GATE[1]:
                out["near_gate"].append({"area": area, "tier": 2, "pass_share": round(share, 2),
                                         "gate": _tiers.gate(prof, 2, area)})
    return out


def paired(a: dict, b: dict, key: str = "overall", level: float = LEVEL) -> dict | None:
    """S_a - S_b over the shared resamples: {lo, hi, tied, n}."""
    sa, sb = a["samples"][key], b["samples"][key]
    diffs = [x - y for x, y in zip(sa, sb) if x is not None and y is not None]
    if not diffs:
        return None
    lo, hi = interval(diffs, level)
    return {"lo": lo, "hi": hi, "tied": lo <= 0.0 <= hi, "n": len(diffs)}


def rank_groups(ranked: list[str], boots: dict[str, dict], s_point: dict[str, float | None],
                key: str = "overall", level: float = LEVEL) -> tuple[dict[str, str], list[dict]]:
    """Rank labels ("1", "2=", ...) and the adjacent-pair readout over
    ``ranked`` (already in board order). A group is a chain of adjacent ties.
    Models without a score, or without a bootstrap, get "-"."""
    labels: dict[str, str] = {}
    pairs: list[dict] = []
    scored = [l for l in ranked if s_point.get(l) is not None and l in boots]
    group_start: dict[str, int] = {}
    for i, l in enumerate(scored):
        if i == 0:
            group_start[l] = 1
            continue
        prev = scored[i - 1]
        d = paired(boots[prev], boots[l], key, level)
        tied = bool(d and d["tied"])
        group_start[l] = group_start[prev] if tied else i + 1
        pairs.append({"a": prev, "b": l, "tied": tied, "level": level,
                      "diff": round((s_point[prev] or 0) - (s_point[l] or 0), 2),
                      "lo": None if not d else round(d["lo"], 2),
                      "hi": None if not d else round(d["hi"], 2)})
    sizes: dict[int, int] = {}
    for g in group_start.values():
        sizes[g] = sizes.get(g, 0) + 1
    for l in ranked:
        g = group_start.get(l)
        labels[l] = "-" if g is None else (f"{g}=" if sizes[g] > 1 else str(g))
    return labels, pairs


def readout(p: dict) -> str:
    sym = "≈" if p["tied"] else "beats"
    pct = round(100 * p.get("level", LEVEL))
    rng = ("" if p.get("lo") is None
           else f" (paired {pct}% interval {p['lo']:+.1f} to {p['hi']:+.1f})")
    return f"{p['a']} {sym} {p['b']}{rng}"
