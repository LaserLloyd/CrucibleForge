"""Bonus pack points ON TOP (owner 2026-11-25, re-confirming R37/R38): totals, maxima, the pack list and the ranges.

"Yes, add the points on top and count in the rank. For the frontier models we have to be able to show how much
better they are compared to each other; we'll have to add the bonus points to the top as they keep going up in the
future." The 4.5.0 rule (pack points shown apart, never added) is withdrawn; this is suite 4.1's model:

* Code total = Code standard points + Code pack points; Chat likewise; Overall = Code total + Chat total
  ("Total 24.6 /28 = 19.3 + 5.3 bonus"). The rank and the ties are on the totals.
* The maximum with packs is the standard maximum plus every pack that exists today for the group (each pack set,
  level or pack is worth its own points, 1 by default), derived from the packs listed, never hard-coded.
* A pack that unlocked but was not run (or lost its rows) counts 0 for now and is listed as owed; the row is still
  ranked on what is measured (a check never withholds a score), and its notes say so.
* The adult page (NSFW, Uncensored) follows the same rule inside its own two numbers and never enters Overall.

Ranges: a total's range is measured from the replicate runs when the board has them (each run's total, Student-t /
pooled as accuracy45), else the standard range and the pack ranges are added in quadrature (a pack without a range
of its own is named). The standard score keeps its own range beside it: the 2.5% accuracy bar was set for it.

This module decides printed numbers: it is in ``segments45.SCORER_FILES``. It reads the pack figures the report
already computes (enginepacks45, packs45, nsfwpack45); it scores nothing itself and never touches a stored row.
"""
from __future__ import annotations

import math

#: per-pack states printed on the board and in report.json
EARNED, LOCKED, NOT_RUN = "earned", "locked", "unlocked, not run"
#: the score keys of a group and its section in the score tree
GROUPS = (("work", "code"), ("chat", "chat"))
#: adult page halves: nsfwpack45 gate number -> section key in the accuracy block
ADULT = ("nsfw", "uncensored")
#: label of the range of a total and of the standard score (the report says which is which)
TOTAL_RANGE_LABEL = "range of the total (standard + bonus)"
STANDARD_RANGE_LABEL = "range of the standard score (the 2.5% accuracy bar applies to it)"


def _state(states: dict) -> str:
    vals = list(states.values())
    if any(v == "scored" for v in vals):
        return EARNED
    if any(v in ("pending", "not run") for v in vals):
        return NOT_RUN
    return LOCKED


def ladder_packs(key: str, ladder_card: dict | None, eng_parts: dict | None, fix: dict | None,
                 unlock: float, skip_parts=()) -> list[dict]:
    """The ladder (engine) and Fixes packs of one group: one entry per part, its levels, the unlock threshold, the
    model's own figure on the part, the state and the points out of the pack's maximum. The points are the ones the
    report adds (enginepacks45 for the parts it gates on the top of the range, the ladder card for the rest)."""
    from . import enginepacks45 as _ep
    from . import ladder as _ladder
    out = []
    for pk, det in ((ladder_card or {}).get(key) or {}).items():
        part = f"{key}/{pk}"
        sets = [s for s in det.get("sets") or [] if isinstance(s, dict) and "n" in s]
        if not sets or part in skip_parts:
            continue
        pts = {s["n"]: float(s.get("points", 1.0) or 0.0) for s in sets}
        lost = sum(int(s.get("n_excluded") or 0) for s in sets)
        if part == _ep.FIXES_PACK_PART:
            f = fix or {}
            states = {int(n): x for n, x in (f.get("sets") or {}).items()}
            figure = f.get("gate_top")
            points = float(f.get("points") or 0.0) if f.get("state") != "locked" else 0.0
        elif part in (eng_parts or {}):
            seq, shares = _ep._seq(det)
            figure = eng_parts[part]["gate_top"]
            states = _ladder.states(figure, shares, seq, unlock) if seq else {}
            points = float(eng_parts[part]["points"])
        else:
            figure = det.get("unlock_share", det.get("std_share"))
            states = {s["n"]: s.get("state") for s in sets}
            points = sum(pts[s["n"]] * float(s.get("share") or 0.0) for s in sets if s.get("state") == "scored")
        out.append({"part": part, "name": str(det.get("label") or pk), "kind": "ladder",
                    "levels": [{"n": n, "state": states.get(n, "locked"), "max": pts[n]} for n in sorted(pts)],
                    "unlock": unlock, "figure": None if figure is None else round(float(figure), 4),
                    "state": _state(states), "points": round(points, 4), "max": round(sum(pts.values()), 4),
                    **({"lost_rows": lost} if lost else {})})
    return out


def chat_packs(cp: dict | None, unlock: float, labels: dict | None = None) -> list[dict]:
    """The Chat packs (packs45.board_packs): per segment a judged level and a generated level, 1 point each."""
    out = []
    st_map = {"scored": EARNED, "pending": NOT_RUN, "locked": LOCKED}
    for seg, v in (cp or {}).items():
        out.append({"part": f"chat/{v.get('part') or seg}", "name": str((labels or {}).get(seg) or seg),
                    "kind": "chat", "unlock": unlock, "figure": v.get("gate_top"),
                    "state": st_map.get(v.get("state"), LOCKED), "points": round(float(v.get("points") or 0.0), 4),
                    "max": 2.0, "range": v.get("range")})
    return out


def adult_packs(board: dict | None, unlock: float) -> dict[str, list[dict]]:
    """{"nsfw": [...], "uncensored": [...]}: the NSFW+ packs (nsfwpack45.board_packs), 1 point each, under the half
    whose part they extend."""
    from . import nsfwpack45 as _npk
    out: dict = {h: [] for h in ADULT}
    st_map = {"scored": EARNED, "pending": NOT_RUN, "not run": NOT_RUN, "locked": LOCKED}
    board = board or {}
    # every pack that exists today counts toward the maximum, run or not (a pack missing from the board is locked)
    for pk in list(_npk.PACKS) + [k for k in board if k not in _npk.PACKS]:
        v = board.get(pk) or {"state": "locked", "points": 0.0}
        half = (_npk.GATE_OF.get(pk) or ("nsfw",))[0]
        half = "uncensored" if str(half).startswith("unc") else "nsfw"
        out[half].append({"part": f"nsfw/{pk}", "name": str(v.get("label") or pk), "kind": "adult",
                          "unlock": unlock, "figure": v.get("gate_share"),
                          "state": st_map.get(v.get("state"), LOCKED),
                          "points": round(float(v.get("points") or 0.0), 4), "max": 1.0})
    return out


def owed(packs: list[dict]) -> list[str]:
    """The packs that unlocked but have no score yet (not run, or every row lost): "part (name)"."""
    return [f"{p['part']} ({p['name']})" for p in packs if p["state"] == NOT_RUN]


def group_total(std: float | None, std_max: float, packs: list[dict], pack_points: float | None = None) -> dict:
    """{standard, bonus, total, max, max_with_packs, packs, owed}. ``pack_points`` = the points the report already
    added for the group (authoritative); the listed packs give the maximum and the split, and any difference is
    listed as "other pack points" so the split always adds up."""
    listed = sum(p["points"] for p in packs)
    bonus = listed if pack_points is None else float(pack_points)
    if abs(bonus - listed) > 0.005:
        packs = packs + [{"part": "-", "name": "other pack points", "kind": "other", "unlock": None, "figure": None,
                          "state": EARNED if bonus > listed else LOCKED, "points": round(bonus - listed, 4),
                          "max": 0.0}]
    mx = float(std_max) + sum(float(p["max"]) for p in packs)
    return {"standard": None if std is None else round(float(std), 4), "bonus": round(bonus, 4),
            "total": None if std is None else round(float(std) + bonus, 4), "max": float(std_max),
            "max_with_packs": round(mx, 4), "packs": packs, "owed": owed(packs)}


def _pack_half(packs: list[dict]) -> tuple[float, list[str]]:
    """(the packs' combined range half-width in points, names of earned packs with no range of their own)."""
    sq, none = 0.0, []
    for p in packs:
        if p["state"] != EARNED or p["points"] <= 0:
            continue
        r = p.get("range")
        if isinstance(r, (int, float)) and not isinstance(r, bool):
            sq += float(r) ** 2
        else:
            none.append(p["name"])
    return math.sqrt(sq), none


#: the run-to-run keys of each total's pack points (resolution45.report_points); Uncensored has no pack key of its own
PACK_KEYS = {"code": ("code_packs",), "chat": ("chat_packs",), "overall": ("code_packs", "chat_packs"),
             "nsfw": ("nsfw_packs",), "uncensored": (), "nsfw_plus": ("nsfw_packs",)}


def total_range(key: str, std_block: dict | None, packs: list[dict], max_with_packs: float, label: str | None,
                rtr: dict | None, st: dict, bonus: float | None = None, pack_keys=None) -> dict | None:
    """The range printed beside a total (coordinator 2026-11-26: a model with no pack point has no pack noise).

    1. No pack point earned (``bonus`` 0): the standard range itself, on the scale with packs.
    2. The model's own replicate totals from ``accuracy45.OWN_MIN_RUNS`` runs on (``<key>_total``).
    3. Else the standard range and the pack noise in quadrature: the run-to-run spread of each earning group's pack
       points (``pack_keys``, own or pooled over the replicate runs as accuracy45, Student t), else the packs' own
       ranges (a pack without one is named). A pooled TOTAL spread is never applied to a model on its own: it mixes
       the replicated models' pack-gate flips into rows that may have no packs."""
    from . import accuracy45 as _A
    if not std_block or std_block.get("half") is None:
        if rtr and label:                                   # no standard range: the replicate totals, if any
            r = _A._replicate_block(f"{key}_total", {"max": max_with_packs}, label, rtr, st)
            return None if r is None else {**r, "label": TOTAL_RANGE_LABEL}
        return None
    sh = float(std_block["half"])
    basis0 = std_block.get("basis") or "standard range"
    if bonus is not None and float(bonus) <= 0.005:
        b = _A.block(sh, max_with_packs, std_block.get("source"), st,
                     basis=basis0 + "; no pack points, so no pack noise", n=std_block.get("n"))
        return {**b, "label": TOTAL_RANGE_LABEL}
    if rtr and label:
        n = int(((rtr.get("n") or {}).get(label) or {}).get(f"{key}_total") or 0)
        if n >= _A.OWN_MIN_RUNS:
            r = _A._replicate_block(f"{key}_total", {"max": max_with_packs}, label, rtr, st)
            if r is not None:
                return {**r, "label": TOTAL_RANGE_LABEL}
    keys = PACK_KEYS.get(key, ()) if pack_keys is None else tuple(pack_keys)
    reps = [(_A._replicate_block(k, {"max": max_with_packs}, label, rtr, st) if rtr and label else None)
            for k in keys]
    note = None
    if keys and all(r is not None and r.get("half") is not None for r in reps):
        ph = math.sqrt(sum(float(r["half"]) ** 2 for r in reps))
        src = "pooled" if any(r.get("source") == _A.POOLED_SRC for r in reps) else "own"
        how = f"pack points' run-to-run spread ({src}, {', '.join(keys)}) in quadrature"
    else:
        ph, none = _pack_half(packs)
        note = (f"no pack range measured for {', '.join(none)}" if none else None)
        how = "pack ranges in quadrature" if ph else ""
    half = math.sqrt(sh ** 2 + ph ** 2)
    b = _A.block(half, max_with_packs, std_block.get("source"), st, note,
                 basis=basis0 + (f", {how}" if how else ""), n=std_block.get("n"))
    return {**b, "label": TOTAL_RANGE_LABEL}


# ------------------------------------------------------------------ rank rule for the totals (coordinator 2026-11-26)
#: the method note printed under the 4.5 board
RANK_RULE_NOTE = ("Rank = 1 + the number of models clearly better on the total (their 90% paired interval of the "
                  "difference of totals lies above zero); models with the same place share it with \"=\". Ties never "
                  "chain: \"3=\" with another model means that pair itself is not separable. Down the list a place is "
                  "never better than the one above it. The difference of totals is tested on {how}.")
RANK_HOW_BOOT = ("the paired bootstrap over the questions (both models answered the same ones), with each model's "
                 "pack noise added as an independent draw (pack noise = what the total's range adds over the "
                 "standard range)")
RANK_HOW_RANGES = "the two totals' ranges combined in quadrature (no paired data for the pair)"


def pack_sd(total_acc: dict | None, std_acc: dict | None) -> float:
    """A total's pack noise as an SD (points): sqrt(total half^2 - standard half^2) / 1.645; 0 with no pack range."""
    from .accuracy45 import Z90
    th = (total_acc or {}).get("half")
    if th is None:
        return 0.0
    sh = float((std_acc or {}).get("half") or 0.0)
    return math.sqrt(max(float(th) ** 2 - sh * sh, 0.0)) / Z90


def total_samples(label: str, samples: list, total: float, sd: float) -> list:
    """Bootstrap samples of the standard score re-centred on the total, plus an independent pack draw per sample
    (seeded by the label, so a board rebuilds the same). The pairing over questions is kept."""
    import random
    ok = [x for x in samples if x is not None]
    if not ok:
        return list(samples)
    mu = sum(ok) / len(ok)
    rnd = random.Random(f"totals45:{label}")
    return [None if x is None else x - mu + float(total) + (rnd.gauss(0.0, sd) if sd > 0 else 0.0) for x in samples]


def clearly_better(a: str, b: str, samples: dict, totals: dict, halves: dict, level: float) -> tuple[bool, str]:
    """(a is clearly better than b, the method used): the paired interval of total_a - total_b lies above zero.
    Paired bootstrap when both have samples, else the two totals' 90% ranges in quadrature."""
    from . import headline as _h
    sa, sb = samples.get(a), samples.get(b)
    if sa and sb:
        d = _h.paired({"samples": {"overall": sa}}, {"samples": {"overall": sb}}, "overall", level)
        if d is not None:
            return d["lo"] > 0.0, "boot"
    ta, tb = totals.get(a), totals.get(b)
    ha, hb = halves.get(a), halves.get(b)
    if ta is None or tb is None or ha is None or hb is None:
        return False, "none"
    return float(ta) - float(tb) > math.sqrt(float(ha) ** 2 + float(hb) ** 2), "ranges"


def clear_ranks(order: list[str], samples: dict, totals: dict, halves: dict, level: float) -> dict:
    """{labels, better, methods} over ``order`` (already sorted by total, best first; rows without a total get "-").

    place = 1 + the number of models clearly better; printed rank = the running maximum down the list. A row that
    would share a place with a model it is separable from takes the next free place (1 + every row above it), so
    "A= with B" always means the pair A, B itself is not separable, never through a third model."""
    scored = [l for l in order if totals.get(l) is not None]
    better: dict[str, list[str]] = {}
    methods: set[str] = set()
    sep: dict[tuple[str, str], bool] = {}
    for a in scored:
        for b in scored:
            if a != b:
                ok, how = clearly_better(a, b, samples, totals, halves, level)
                sep[(a, b)] = ok
                methods.add(how)
    printed: dict[str, int] = {}
    prev = 0
    for i, l in enumerate(scored):
        better[l] = [m for m in scored if m != l and sep[(m, l)]]
        r = max(prev, 1 + len(better[l]))
        if any(printed[j] == r and (sep[(j, l)] or sep[(l, j)]) for j in scored[:i]):
            r = i + 1
        printed[l] = prev = r
    sizes: dict[int, int] = {}
    for r in printed.values():
        sizes[r] = sizes.get(r, 0) + 1
    labels = {l: ("-" if l not in printed else f"{printed[l]}=" if sizes[printed[l]] > 1 else str(printed[l]))
              for l in order}
    return {"labels": labels, "better": better, "methods": sorted(methods - {"none"})}


def rank_note(methods: list[str]) -> str:
    how = (RANK_HOW_BOOT if methods == ["boot"] else RANK_HOW_RANGES if methods == ["ranges"]
           else RANK_HOW_BOOT + "; where a pair has no paired data, " + RANK_HOW_RANGES)
    return RANK_RULE_NOTE.format(how=how)


def split_text(g: dict, digits: int = 1) -> str:
    """``= 19.3 + 5.3 bonus`` (empty when the group earned no pack points)."""
    if g.get("total") is None or float(g.get("bonus") or 0.0) <= 0.05:
        return ""
    return f"= {g['standard']:.{digits}f} + {g['bonus']:.{digits}f} bonus"


def pack_line(p: dict) -> str:
    """One pack on the board: ``Chat: Stays in character (chat/pk_character): earned 1.0 of 2; unlocks at 90%,
    the model 94%``."""
    fig = p.get("figure")
    gate = ("" if p.get("unlock") is None else f"; unlocks at {float(p['unlock']) * 100:.0f}%"
            + ("" if fig is None else f", the model {float(fig) * 100:.0f}%"))
    pts = (f"{p['points']:.1f} of {p['max']:g}" if p["state"] == EARNED
           else f"0 of {p['max']:g} for now (owed)" if p["state"] == NOT_RUN else f"0 of {p['max']:g}")
    lost = f"; {p['lost_rows']} row(s) lost" if p.get("lost_rows") else ""
    return f"{p['name']} ({p['part']}): {p['state']}, {pts}{gate}{lost}"
