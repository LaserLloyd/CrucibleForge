"""Score tree of the 4.5 realignment: Code /10 and Chat /10 as segments (report-time only).

Overall = Code + Chat. Every segment is a list of *parts* (a part = one stored scoring unit of the engine:
a chain, a flat set, one 4.1 scene case) with a point weight; the segment's points are the sum over its parts of
``share x weight``. Nothing here reads a row or calls a model: it reads the numbers a model's report block already
holds (``headline``, ``chain_stats``, ``rpdeep.per_case``, ``assistant45``), so the stored rows re-score under the
new tree with no regeneration and no stamp touched.

Two parts read inputs the report attaches from outside the headline: the eight 4.1 scenes (``rplong45``: judge and
second reader, ``rplong_mean``) and the fix tasks (``fix45``: rows stamped by the bug-fix checker, ``fixrows45``).
A part without its input reports **pending: <what is missing>**: never 0, never dropped. The
segment then shows its measured points of its measured maximum and names the pending points, exactly like the
older pending convention of the board. Overall is a number only when both sections are complete.

The tree is declared in ``profiles/bench45.yaml`` (composition ``standard``, key ``segments45``).
"""
from __future__ import annotations

import functools
import hashlib
import math
from pathlib import Path
from typing import Any

# the lanes that deliver a stubbed scorer. Each is looked up lazily, so a missing module is "pending", not an error
_W2 = "judge results of the eight chat scenes"
_W3 = "bug-fix checker"

SECTIONS = ("code", "chat")
_SEG_META = ("label", "max", "parts", "cases", "case_repeats", "driver", "units_equal", "weights_are", "slices")


def tree(profile: dict | None) -> dict | None:
    """The profile's ``segments45`` block (composition-merged by the loader), or None."""
    seg = (profile or {}).get("segments45")
    return seg or None


def _full_key(part: str, model: dict) -> str | None:
    """``c1_implied`` -> ``work/c1_implied``: the key the engine's headline and chain stats use."""
    mx = (model.get("headline") or {}).get("max") or {}
    for sect in ("work", "chat"):
        if f"{sect}/{part}" in mx:
            return f"{sect}/{part}"
    return None


class Pending(str):
    """A reason a part has no number yet."""


def _part_share(model: dict, part: str) -> tuple[float | None, str | None]:
    """(standard share 0..1, None) for a measured part, or (None, reason). Ladder packs never enter a share."""
    key = _full_key(part, model)
    if key is None:
        return None, f"pending: no scorer for {part} in this report"
    hl = model.get("headline") or {}
    nm = (hl.get("not_measured") or {}).get(key)
    if nm:
        return None, "pending: not measured (too many replies ran out)" if nm is True or isinstance(nm, (int, float)) \
            else f"pending: not run: {nm}"
    cs = (model.get("chain_stats") or {}).get(key) or {}
    units, ended = cs.get("units"), cs.get("ended")
    if cs.get("share") is not None:
        if units and ended is not None and ended < units:
            return None, f"pending: {units - ended} of {units} units not finished"
        return float(cs["share"]), None
    # the standard share comes from the part's own standard rows (the scorecard's ladder detail), never from the
    # headline P: on the points board P already holds the part's ladder pack points (counted apart)
    cat, pk = key.split("/", 1)
    sh = ((((model.get("scorecard") or {}).get("ladder") or {}).get(cat) or {}).get(pk) or {}).get("std_share")
    if sh is None:
        why = {}
        for sect in ((model.get("measured") or {}).values()):
            for it in (sect or {}).get("pending") or []:
                why[it.get("part")] = it.get("reason")
        r = why.get(part)
        return None, f"pending: {r}" if r else f"pending: {part} has no result yet"
    sh = float(sh)
    if sh > 1.0 + 1e-9 or sh < -1e-9:
        raise ValueError(f"standard share of {part} is {sh:.3f}, outside 0..1: a pack or a bad row leaked in")
    return sh, None


def _scene(model: dict, case: str) -> tuple[float | None, str | None]:
    """One 4.1 scene's score = the judge's fact rate, mean over the scene's repeats (11b, lead 2026-11-21: Chat is
    graded by the one judge alone). The report fills ``model["rplong45"]`` from the stored rows
    (``report._rplong45``); a scene with no scorable judge verdict is "not judged yet"."""
    blk = model.get("rplong45")
    if blk is None:
        return None, f"pending: {_W2}: reader results not loaded"
    sc = (blk.get("scenes") or {}).get(case)
    if sc is None:
        return None, "pending: not run or not judged yet"
    if sc.get("score") is None:
        return None, sc.get("reason") or "pending: not judged yet"
    return float(sc["score"]), None


def _fix_case(model: dict, case: str, n: int = 1) -> tuple[float | None, str | None]:
    """One fix-task unit (repeat ``n`` of the case) = the bug-fix checker's stored score (``fixcheck``,
    host-compared), read from the rows by ``fixrows45.case_scores``; a row graded by an older checker is pending."""
    blk = model.get("fix45")
    if blk is None:
        return None, f"pending: {_W3}: fix rows not loaded"
    cs = (blk.get("cases") or {}).get(case)
    if not cs:
        return None, "pending: no fix rows yet"
    scores = cs.get("scores") or []
    if n <= len(scores):
        return float(scores[n - 1]), None
    return None, cs.get("reason") or f"pending: repeat {n} has no fix row yet"


def _everyday_basics(model: dict) -> tuple[float | None, str | None]:
    a = model.get("assistant45") or {}
    if a.get("status") == "ok" and a.get("share") is not None:
        return float(a["share"]), None
    return None, "pending: Assistant basics not run" if not a else f"pending: Assistant basics {str(a.get('status')).replace('_', ' ')}"


def _unit(model: dict, part: str, spec: Any) -> list[tuple[str, float, float | None, str | None]]:
    """One declared part -> [(unit id, weight, share, reason)]. A dict spec = per-case weights inside a part."""
    if isinstance(spec, dict):
        return [(c, float(w), *(_scene(model, c))) for c, w in spec.items()]
    if part == "assistant":
        return [(part, float(spec), *_everyday_basics(model))]
    return [(part, float(spec), *_part_share(model, part))]


#: plain names for the NSFW+ units a pending reason may name (nsfwplus45 words them with its internal ids)
NSFW_UNIT_NAMES = {"erotica_written": "Erotica (written)", "erotica_dwell": "Erotica (dwell)",
                   "roleplay_session": "Role-play sessions", "roleplay_probe": "Role-play probes",
                   "track_sessions": "Role-play tracking", "track_probes": "Role-play tracking",
                   "range": "Range", "anatomy": "Anatomy", "u1": "Willingness (asked)", "dark": "Willingness (dark)",
                   "u2": "Willingness (straight answers)", "u4": "Willingness (grounded)", "u3": "Knowledge"}


def part_names(profile: dict | None) -> dict[str, str]:
    """{part id: the label of the segment that holds it} from the profile's score tree (``c5_tools`` -> "Uses
    tools"): the board never shows an internal part id, it shows the segment's own words."""
    out: dict[str, str] = {}
    for sk in SECTIONS:
        for seg in ((tree(profile) or {}).get(sk) or {}).values():
            if isinstance(seg, dict):
                for part in (seg.get("parts") or {}):
                    if part != "rplong":
                        out[part] = str(seg.get("label"))
    return out


def plain_text(text: str, names: dict[str, str] | None = None) -> str:
    """``text`` with every internal part id (``c5_tools``, ``work/c5_tools``, ``chat/h6_know``, the NSFW+ unit names)
    replaced by its plain name. Ids that are not known are left alone."""
    import re
    m = {**NSFW_UNIT_NAMES, **(names or {})}
    if not text or not m:
        return text
    pat = re.compile(r"(?<![\w-])(?:(?:work|chat|nsfw)/)?(" + "|".join(
        re.escape(k) for k in sorted(m, key=len, reverse=True)) + r")(?![\w-])")
    return pat.sub(lambda x: m[x.group(1)], text)


Z90 = 1.645


def binomial_margin(points: float, p: float, n: int) -> float | None:
    """The fallback 90% margin where no per-unit spread is stored: 1.645 x points x sqrt(p (1 - p) / n) over the
    part's items (``calibration/marks-evaluation-45-addendum-20261120.json``, key
    ``margins.fallback_without_spread``: "Where no per-unit spread is stored (long scenes, fix tasks, Everyday
    parts), the binomial bound 1.645 x points x sqrt(p(1-p)/n) over the part's items ... For 0 or 1 unit scores
    the two formulas are the same number"). p = the part's mean share, n = its items, points = the part's points.
    A computed 0 (p = 0 or 1) is a stored margin (same file, ``margins.no_margin_zero``; lead ruling R3); None
    only when there is no item to measure."""
    if n < 1:
        return None
    p = min(1.0, max(0.0, float(p)))
    return Z90 * float(points) * math.sqrt(p * (1.0 - p) / n)


def _unit_margin(model: dict, u: dict, hl_m: dict, seg_max: float) -> float | None:
    """The 90% margin (points) a measured unit adds to its segment, or None when none is stored. A part of the
    headline: the headline's own bootstrap margin (a stored 0 is read as none, as before). A scene (its items =
    its repeats) and the Everyday basics (its items = the 15 checked items) carry the addendum's binomial bound
    (:func:`binomial_margin`; lead ruling R2). The fix tasks are pooled over the segment in :func:`_segment`."""
    if u.get("part") and _full_key(u["part"], model):
        return float(hl_m.get(_full_key(u["part"], model)) or 0.0) or None
    w, p = float(u["weight"]), float(u["share"])
    if u.get("part") == "assistant":
        return binomial_margin(w, p, int((model.get("assistant45") or {}).get("n_items") or 0))
    sc = ((model.get("rplong45") or {}).get("scenes") or {}).get(u["id"])
    return binomial_margin(w, p, max(1, int((sc or {}).get("repeats") or 1)))


def case_unit_weights(seg: dict) -> dict[str, float]:
    """{case id: the points ONE unit (one repeat) of that case carries} for a fix-task segment. Without ``slices``
    every unit weighs the same (max / units, repeats counted). With ``slices`` ({name: {points, match: [id
    prefixes]}}, step 2 of 4.5) each family keeps its own points and the units inside a slice are equal: a case's
    unit = its slice's points / the slice's units. Every case sits in exactly one slice and the slices add up to the
    segment's max, or this raises."""
    mx = float(seg["max"])
    reps = seg.get("case_repeats") or {}
    cases = list(seg.get("cases") or [])
    n = {c: int(reps.get(c, 1)) for c in cases}
    sl = seg.get("slices")
    if not sl:
        tot = sum(n.values())
        return {c: mx / tot for c in cases}
    if abs(sum(float(v["points"]) for v in sl.values()) - mx) > 1e-9:
        raise ValueError(f"fix slices add up to {sum(float(v['points']) for v in sl.values())}, not {mx}")
    of: dict[str, str] = {}
    for c in cases:
        hit = [k for k, v in sl.items() if any(c.startswith(pre) for pre in v.get("match") or [])]
        if len(hit) != 1:
            raise ValueError(f"fix case {c} sits in {len(hit)} slices (needs exactly one)")
        of[c] = hit[0]
    out = {}
    for k, v in sl.items():
        units = sum(n[c] for c in cases if of[c] == k)
        if not units:
            raise ValueError(f"fix slice {k} has no case")
        for c in cases:
            if of[c] == k:
                out[c] = float(v["points"]) / units
    return out


def _segment(model: dict, key: str, seg: dict, hl_m: dict) -> dict:
    mx = float(seg["max"])
    units: list[dict] = []
    if seg.get("cases"):                                   # fix tasks: equal units within a slice, repeats counted
        reps = seg.get("case_repeats") or {}
        ids = [c for c in seg["cases"] for _ in range(int(reps.get(c, 1)))]
        cw = case_unit_weights(seg)
        seen: dict[str, int] = {}
        for i, c in enumerate(ids):
            seen[c] = seen.get(c, 0) + 1
            sh, why = _fix_case(model, c, seen[c])
            units.append({"id": c, "n": i + 1, "weight": cw[c], "share": sh, "reason": why})
    else:
        raw = []
        for part, spec in (seg.get("parts") or {}).items():
            for uid, wt, sh, why in _unit(model, part, spec):
                raw.append((part, uid, wt, sh, why))
        total = sum(r[2] for r in raw)
        pts_mode = seg.get("weights_are") == "points"
        for part, uid, wt, sh, why in raw:
            w = wt if pts_mode else (wt / total * mx if total else 0.0)
            units.append({"id": uid, "part": part, "weight": w, "share": sh, "reason": why})
    got = [u for u in units if u["share"] is not None]
    pend = [u for u in units if u["share"] is None]
    points = sum(u["weight"] * u["share"] for u in got)
    # N9: the margin carries every measured unit's own part margin: the parts of the headline, and the scenes,
    # fix tasks and Everyday basics, which have no headline margin of their own (their sampling spread is the
    # binomial one over their items; see _unit_margin)
    if seg.get("cases"):
        # fix tasks: one part over the segment's units (repeats counted): p = their weighted mean share, and the bound
        # 1.645 x sqrt(p (1 - p) x sum of w^2), which is binomial_margin(sum w, p, n) when the units weigh the same
        # (step 2: slices give the families unequal unit weights)
        if got:
            wsum = sum(u["weight"] for u in got)
            p_ = sum(u["weight"] * u["share"] for u in got) / wsum if wsum else 0.0
            p_ = min(1.0, max(0.0, p_))
            m2 = [Z90 * math.sqrt(p_ * (1.0 - p_) * sum(u["weight"] ** 2 for u in got))]
        else:
            m2 = []
    else:
        m2 = [_unit_margin(model, u, hl_m, mx) for u in got]
    m2 = [x for x in m2 if x is not None]
    ck = {k for k in (_full_key(u["part"], model) for u in units if u.get("part")) if k}
    cut = code_chat_cut(model.get("headline") or {}, ck)
    return {
        "label": seg.get("label", key), "max": mx,
        "points": round(points, 4) if got else None,
        "measured_max": round(sum(u["weight"] for u in got), 4),
        "pending_points": round(sum(u["weight"] for u in pend), 4),
        "pending": [{"unit": u["id"], "points": round(u["weight"], 4), "reason": u["reason"]} for u in pend],
        "complete": not pend,
        "margin": round(math.sqrt(sum(x * x for x in m2)), 2) if got and m2 else None,
        "cut": cut,
        "units": [{"id": u["id"], "weight": round(u["weight"], 4),
                   "share": None if u["share"] is None else round(u["share"], 4)} for u in units],
    }


def _section(label: str, segs: dict) -> dict:
    mxs = sum(s["max"] for s in segs.values())
    got = [s for s in segs.values() if s["points"] is not None]
    m2 = [s["margin"] for s in got if s["margin"] is not None]
    return {
        "label": label, "max": mxs,
        "points": round(sum(s["points"] for s in got), 4) if got else None,
        "measured_max": round(sum(s["measured_max"] for s in segs.values()), 4),
        "pending_points": round(sum(s["pending_points"] for s in segs.values()), 4),
        "complete": all(s["complete"] for s in segs.values()),
        "margin": round(math.sqrt(sum(x * x for x in m2)), 2) if m2 else None,
        "segments": segs,
    }


def segment_table(model_report: dict, profile: dict | None, marks: dict | None = None) -> dict | None:
    """``{code: {label, max, points, measured_max, pending, complete, segments: {...}}, chat: {...}, overall: {...}}``
    for one model's report block. None when the profile declares no ``segments45``. ``marks`` = :func:`load_marks`
    of the results folder: a segment whose governing marks failed or were never applied there is held pending
    (:func:`apply_marks`)."""
    t = tree(profile)
    if not t:
        return None
    hl_m = (model_report.get("headline") or {}).get("m") or {}
    out: dict = {}
    for sk in SECTIONS:
        sect = t.get(sk)
        if not sect:
            continue
        segs = {k: _segment(model_report, k, v, hl_m) for k, v in sect.items() if isinstance(v, dict)}
        out[sk] = _section(sect.get("label", sk.title()), segs)
    names = part_names(profile)
    for sect in out.values():
        for g in sect["segments"].values():
            for p in g["pending"]:
                p["reason"] = plain_text(p["reason"], names)
    if marks is not None:
        apply_marks(out, marks)
    else:
        out["overall"] = overall(out)
    return out


def overall(table: dict) -> dict:
    """Overall = Code + Chat. A number (the sum) only when both sections are complete; the measured sum and the
    reasons are always carried so the board can say what is missing."""
    secs = [table[k] for k in SECTIONS if k in table]
    got = [s for s in secs if s["points"] is not None]
    pend = [f"{s['label']}: {g['label']} ({'; '.join(sorted({p['reason'] for p in g['pending']}))})"
            for s in secs for g in s["segments"].values() if g["pending"]]
    complete = bool(secs) and all(s["complete"] for s in secs)
    pts = sum(s["points"] for s in got) if got else None
    m2 = [s["margin"] for s in got if s["margin"]]
    reasons = sorted({p["reason"] for s in secs for g in s["segments"].values() for p in g["pending"]})
    return {"pending_reasons": reasons, "max": sum(s["max"] for s in secs),
            "points": None if not complete else round(pts, 4),
            "measured_points": None if pts is None else round(pts, 4),
            "measured_max": round(sum(s["measured_max"] for s in secs), 4),
            "complete": complete, "pending": pend,
            "margin": round(math.sqrt(sum(x * x for x in m2)), 2) if m2 and complete else None}


# ------------------------------------------------------------------ marks: what governs each segment
#: A mark is a pre-registered check a segment's number is held to (calibration/*-marks-45*.json). Ids are
#: ``<group>:<name>``; the group says where the result is read (:func:`load_marks`).
#:   segment:<seg>   marks45.json ``segments[<seg>]``      (segment-marks-45.json: the ceiling / known-order mark)
#:   everyday:EVn    marks45.json ``everyday[EVn_*]``     (everyday-marks-45.json; EV3 = assistant_marks.json)
#:   act_beat:ABn    marks45.json ``act_beat[ABn_*]``     (act-beat-marks-45.json)
#:   classing:Cn     marks45.json ``classing[Cn_*]``      (nsfw-classing-marks-45.json, scripts/check_classing_marks.py)
#:   chat_rplong     marks45.json ``chat_rplong``         (long-scene judge-term mark, scripts/check_rplong_marks45.py)
#:   assistant       <results>/assistant_marks.json       (the frozen Assistant-basics marks)
#: The Chat PACK marks (P1-P4, calibration/pack-marks-chat-45-addendum-20261120.json) hold no segment: they are in
#: marks45.json ``chat_packs`` and gate only the Pack points column (:func:`load_pack_marks`, packs45.board_packs).
#: C7 (opinion flags) does NOT hold a segment: while it is not passed the three opinion questions score on their
#: code checks and the board says so (:func:`notes`).
_CL = tuple(f"classing:C{i}" for i in range(1, 7))
_AB = tuple(f"act_beat:AB{i}" for i in range(1, 6))
_EV = tuple(f"everyday:EV{i}" for i in range(1, 6))
SEGMENT_MARKS: dict[str, tuple[str, ...]] = {
    "code/writes": ("segment:code/writes",),
    "code/fixes": ("segment:code/fixes",),
    "code/tools": ("segment:code/tools",),
    "code/reads": ("segment:code/reads",),
    "chat/character": ("segment:chat/character", "chat_rplong"),
    "chat/remembers": ("segment:chat/remembers", "chat_rplong"),
    "chat/cowrites": ("segment:chat/cowrites", "chat_rplong"),
    "chat/everyday": ("segment:chat/everyday", *_EV, "assistant"),
    # Part 1 (owner 2026-11-21): one local judge classes the adult page; the reader marks C1-C6 (admission,
    # agreement, seeds, quote, third-reader load, reader removal) no longer hold any segment (``_CL`` is kept for
    # the calibration history and its labels only)
    "nsfw/erotica": ("segment:nsfw/erotica", *_AB),
    "nsfw/roleplay": ("segment:nsfw/roleplay", *_AB),
    "nsfw/range": ("segment:nsfw/range", *_AB),
    "nsfw/anatomy": ("segment:nsfw/anatomy", *_AB),
    "unc/willingness": ("segment:unc/willingness", *_AB),
    "unc/knowledge": ("segment:unc/knowledge",),
}
#: plain-word names for the board (no ids)
MARK_LABEL = {
    "chat_rplong": "long-scene judge-term mark", "assistant": "Assistant-basics marks",
    "classing:C1": "classing readers' constructed-set check", "classing:C2": "classing reader agreement",
    "classing:C3": "classing seed stability", "classing:C4": "classing quote check",
    "classing:C5": "third-reader load", "classing:C6": "classing reader refusal",
    "act_beat:AB1": "explicit-wrongly-zeroed check", "act_beat:AB2": "fade backstop check",
    "act_beat:AB3": "both-ways act key fixtures", "act_beat:AB4": "beats-separate check",
    "act_beat:AB5": "no-taste-points check",
    "everyday:EV1": "Everyday reference check", "everyday:EV2": "Everyday damaged-model gap",
    "everyday:EV3": "Everyday basics marks", "everyday:EV4": "Everyday know-contrast check",
    "everyday:EV5": "Everyday floor disclosure",
}
MISSING_REASON = "marks not applied to this folder yet"
_CAL = None


def _cal(name: str) -> dict:
    """A calibration file read through the marks index (an edited or unindexed copy is not read: {} then)."""
    import json
    from pathlib import Path
    from . import marksindex45
    p = Path(__file__).resolve().parents[1] / "calibration" / name
    try:
        marksindex45.require(p)
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def describe(mark: str) -> str:
    """The mark's own description from its calibration file (rule text), or its plain label."""
    grp, _, name = mark.partition(":")
    if mark == "chat_rplong":
        from . import rplong_marks
        try:                                    # the one hashed loader of the long-scene marks
            d = (rplong_marks.load_marks().get("primary") or {})
        except (OSError, ValueError, rplong_marks.MarksHashMismatch):
            d = {}
        return str(d.get("_why") or MARK_LABEL[mark])
    if grp == "segment":
        e = (_cal("segment-marks-45.json").get("segments") or {}).get(name) or {}
        pr = e.get("primary")
        return f"known order {pr[0]} {pr[1]} {pr[2]}" if pr else MARK_LABEL.get(mark, "known-order mark")
    fname = {"classing": "nsfw-classing-marks-45.json", "act_beat": "act-beat-marks-45.json",
             "everyday": "everyday-marks-45.json"}.get(grp)
    if fname:
        for k, v in (_cal(fname).get("marks") or {}).items():
            if k == name or k.startswith(name + "_"):
                return str(v.get("rule") if isinstance(v, dict) else v)
    return MARK_LABEL.get(mark, mark)


def _label(mark: str) -> str:
    grp, _, name = mark.partition(":")
    if grp == "segment":
        return "known-order mark"
    return MARK_LABEL.get(mark, mark)


def _norm(v) -> dict:
    """One stored result -> {status: pass|fail|missing, reason}. Accepts ``{status}``, ``{pass, measured}`` or
    ``{ok, reasons}``; anything unreadable is missing, never a pass."""
    if not isinstance(v, dict):
        return {"status": "missing", "reason": MISSING_REASON}
    st = v.get("status")
    if st in ("pass", "fail"):
        return {"status": st, "reason": v.get("reason") or ""}
    if st in ("not_measured", "not_evaluable") or v.get("measured") is False:
        return {"status": "missing", "reason": v.get("reason") or "not measured yet"}
    # a pass is the JSON value true only: "false", 1 or "yes" are unreadable, so missing (never a pass)
    if "pass" in v:
        if isinstance(v["pass"], bool):
            return {"status": "pass" if v["pass"] else "fail", "reason": str(v.get("reason") or "")}
        return {"status": "missing", "reason": MISSING_REASON}
    if "ok" in v:
        rs = v.get("reasons")
        rs = [str(x) for x in rs] if isinstance(rs, list) else []
        if isinstance(v["ok"], bool):
            return {"status": "pass" if v["ok"] else "fail", "reason": "; ".join(rs)}
        return {"status": "missing", "reason": MISSING_REASON}
    return {"status": "missing", "reason": MISSING_REASON}


def _pick(d, prefix: str):
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k == prefix or k.startswith(prefix + "_"):
            return v
    return None


def load_marks(results) -> dict:
    """{mark id: {status: pass|fail|missing, reason}} for every mark any segment names, read from the results
    folder (``marks45.json`` and ``assistant_marks.json``). A mark with no stored result is ``missing``."""
    import json
    from pathlib import Path
    root = Path(results) if results else None

    def _j(name):
        try:
            return json.loads((root / name).read_text(encoding="utf-8")) if root else {}
        except (OSError, ValueError):
            return {}
    m45, asst = _j("marks45.json"), (_j("assistant_marks.json") if root else {})
    m45 = m45 if isinstance(m45, dict) else {}          # a list or a number at the top is unreadable: no mark
    asst = asst if isinstance(asst, dict) else {}
    out: dict = {}
    for ids in (*SEGMENT_MARKS.values(), _CL, ("classing:C7",)):
        for mid in ids:
            if mid in out:
                continue
            grp, _, name = mid.partition(":")
            if mid == "chat_rplong":
                v = m45.get("chat_rplong")
            elif mid == "assistant":
                v = asst or None
            elif grp == "segment":
                segs_ = m45.get("segments")
                v = segs_.get(name) if isinstance(segs_, dict) else None
            elif grp in ("classing", "act_beat", "everyday"):
                v = _pick(m45.get(grp), name)
                if v is None and mid == "everyday:EV3" and asst:
                    v = asst
            else:
                v = None
            out[mid] = _norm(v)
    return out


def load_pack_marks(results) -> dict | None:
    """The stored ``chat_packs`` block of ``<results>/marks45.json`` (scripts/check_pack_marks45.py), or None when the
    folder has none or it is unreadable (packs then read pending: the board never invents a mark)."""
    import json
    from pathlib import Path
    try:
        d = json.loads((Path(results) / "marks45.json").read_text(encoding="utf-8")) if results else None
    except (OSError, ValueError):
        return None
    v = d.get("chat_packs") if isinstance(d, dict) else None
    return v if isinstance(v, dict) else None


#: marks whose purpose was to separate models (the ceiling / known-order mark of each segment): out of every number,
#: warning and note (INTENT-45: "ties are expected"; SPEC-BUILD-45 "Out": ceiling warnings, known-order checks)
SEPARATION_MARKS = ("segment:",)


def noted_marks(seg_key: str) -> tuple[str, ...]:
    """The marks of a segment that still print as notes (every governing mark but the separation ones)."""
    return tuple(i for i in SEGMENT_MARKS.get(seg_key, ()) if not i.startswith(SEPARATION_MARKS))


def mark_state(seg_key: str, marks: dict) -> dict:
    """{state: applied|failed|missing, marks: [ids], failed: [...], missing: [...], reason}. Failed beats missing."""
    ids = noted_marks(seg_key)
    res = {i: marks.get(i) or {"status": "missing", "reason": MISSING_REASON} for i in ids}
    failed = [i for i, r in res.items() if r["status"] == "fail"]
    missing = [i for i, r in res.items() if r["status"] == "missing"]
    if failed:
        why = "; ".join(f"{_label(i)} not met" + (f" ({res[i]['reason']})" if res[i]["reason"] else "") for i in failed)
        st = "failed"
    elif missing:
        reasons = {res[i]["reason"] for i in missing}
        if reasons == {MISSING_REASON}:
            why = MISSING_REASON
        else:
            why = "; ".join(f"{_label(i)}: {res[i]['reason']}" for i in missing)
        st = "missing"
    else:
        why, st = "", "applied"
    return {"state": st, "marks": list(ids), "failed": failed, "missing": missing, "reason": why}


def hold_segment(g: dict, ms: dict) -> None:
    """Attach a segment's marks as NOTES (INTENT-45, owner 2026-11-21: "Other checks print a note"). A failed or
    missing mark never changes the number, the margin or what is pending: pending stays only for "not run" and
    "not judged yet". The name is kept for callers; nothing is held."""
    g["marks"] = ms
    g["notes"] = mark_notes(ms)


def mark_notes(ms: dict) -> list[str]:
    """The plain note lines of one segment's marks: "note: <mark> not met (...)" / "note: <mark> not checked"."""
    if ms.get("state") == "failed":
        return [f"note: {ms['reason']}"]
    if ms.get("state") == "missing":
        return ["note: " + (ms["reason"] or MISSING_REASON).replace(MISSING_REASON, "marks not checked on this folder")]
    return []


def apply_marks(tb: dict, marks: dict) -> dict:
    """Attach every segment's marks as notes (they hold nothing), then re-total Code, Chat and Overall."""
    for sk in SECTIONS:
        sect = tb.get(sk)
        if not sect:
            continue
        for k, g in sect["segments"].items():
            hold_segment(g, mark_state(f"{sk}/{k}", marks))
        tb[sk] = _section(sect["label"], sect["segments"])
    tb["overall"] = overall(tb)
    return tb


_NP_SEG_OF = {"erotica_written": "erotica", "erotica_dwell": "erotica", "roleplay_session": "roleplay",
              "roleplay_probe": "roleplay", "range": "range", "anatomy": "anatomy", "u1": "willingness",
              "dark": "willingness", "u2": "willingness", "u4": "willingness", "u3": "knowledge"}


def cut_record(rec) -> dict | None:
    """One segment's cut record in the one shape the board prints (``report.cut_words``): ``{left_out,
    did_not_finish, of}`` plus ``dnf_zero`` when a unit did not finish. Takes the scorer's own record: a flat one,
    or NSFW+'s ``{part: record}`` (summed). None when nothing was cut."""
    if not rec:
        return None
    flat = [rec] if ("of" in rec or "left_out" in rec) else [v for v in rec.values() if isinstance(v, dict)]
    lo = sum(int(r.get("left_out") or 0) for r in flat)
    dn = sum(int(r.get("did_not_finish") or 0) for r in flat)
    if not lo and not dn:
        return None
    out = {"left_out": lo, "did_not_finish": dn, "of": sum(int(r.get("of") or 0) for r in flat)}
    if dn:
        out["dnf_zero"] = all(r.get("dnf_zero", True) for r in flat if int(r.get("did_not_finish") or 0))
    return out


def code_chat_cut(headline: dict, keys) -> dict | None:
    """A Code or Chat segment's cut record from the scorer's own per-part records (``report.case_part_rows`` through
    the headline: ``cut`` = units left out of the mean, ``did_not_finish`` = units graded as delivered)."""
    cuts, dnf = headline.get("cut") or {}, headline.get("did_not_finish") or {}
    recs = []
    for k in keys:
        c, d = cuts.get(k) or {}, dnf.get(k) or {}
        if c or d:
            recs.append({"left_out": int(c.get("cut") or 0), "did_not_finish": int(d.get("n") or 0),
                         "of": int(c.get("of") or d.get("of") or 0),
                         **({"dnf_zero": bool(d.get("all_zero", True))} if d.get("n") else {})})
    return cut_record({str(i): r for i, r in enumerate(recs)}) if recs else None


def nsfw_cuts(units: list[dict]) -> dict:
    """{segment: cut record} per NSFW+ segment from units that went through ``nsfwplus45.apply_cut_rule``
    (``status.disposition``): the same record the scorer keeps (kept for callers; the board reads the scorer's)."""
    out: dict = {}
    for u in units or []:
        seg = _NP_SEG_OF.get(u.get("segment"))
        if seg is None:
            continue
        c = out.setdefault(seg, {"left_out": 0, "did_not_finish": 0, "of": 0})
        c["of"] += 1
        d = (u.get("status") or {}).get("disposition")
        c["left_out"] += d == "left_out" or (d is None and bool((u.get("status") or {}).get("cut")))
        c["did_not_finish"] += d == "did_not_finish"
    return out


def nsfw_marks(block: dict, marks: dict, cuts: dict | None = None) -> dict:
    """Attach the NSFW+ segments' marks as notes (the numbers come from ``nsfwplus45.numbers``; wrapped here, report
    side). A mark never sets a value to None; a number is pending only when its own input is not run or not judged
    yet. Adds ``marks`` and ``notes`` per segment."""
    nums = (block or {}).get("numbers")
    if not nums:
        return block
    prefix = {"nsfw": "nsfw", "uncensored": "unc"}
    for nk, num in nums.items():
        segs = num.get("segments") or {}
        for sk, g in segs.items():
            ms = mark_state(f"{prefix[nk]}/{sk}", marks)
            g["marks"] = ms
            if cuts and sk in cuts:
                g["cut"] = cuts[sk]
            if g.get("cut") and not ("of" in g["cut"] or "left_out" in g["cut"]):
                g["cut_parts"] = g["cut"]          # the scorer's own per-part record, kept
            g["cut"] = cut_record(g.get("cut"))
            g["notes"] = mark_notes(ms)            # a note only: the value stands (INTENT-45)
        for g in segs.values():
            if g.get("pending"):
                g["pending"] = plain_text(g["pending"])
        if num.get("pending"):
            num["pending"] = plain_text(num["pending"])
        pend = [g for g in segs.values() if g.get("pending")]
        done = sum(g["value"] for g in segs.values() if g.get("value") is not None)
        num["value"] = None if pend else round(done, 6)
        num["partial"] = round(done, 6)
        num["pending"] = " | ".join(f"{g['label']} {g['pending']}" for g in pend) or None
    block["state"] = "complete" if all(n["value"] is not None for n in nums.values()) else "pending"
    return block


def notes(marks: dict, nsfw_block: dict | None = None) -> list[str]:
    """The plain lines the board prints beside the numbers (not holding any segment)."""
    out = []
    if (marks.get("classing:C7") or {}).get("status") != "pass":
        out.append("opinion flags not validated; scored on code checks")
    why = ((nsfw_block or {}).get("numbers") or {})
    txt = " ".join(str((n or {}).get("pending") or "") for n in why.values()) if why else str((nsfw_block or {}).get("reason") or "")
    if "classing readers" in txt:
        out.append("classing readers not named yet")
    return out


def cell(sect: dict | None, digits: int = 1) -> str:
    """Board cell for a section or segment: ``7.2`` when complete, ``7.2 of 8.5 (pending: ...)`` otherwise."""
    if not sect or sect.get("points") is None:
        return "pending"
    pts = f"{sect['points']:.{digits}f}"
    if sect.get("complete", True) and not sect.get("pending_points"):
        return pts + (f" ± {sect['margin']:.1f}" if sect.get("margin") else "")
    return f"{pts} of {sect['measured_max']:.{digits}f} (pending {sect['pending_points']:g} pts)"


# ------------------------------------------------------------------ scorer stamp
#: the report-time scorers: code that changes published numbers without touching a stored row or a grader stamp
SCORER_FILES = ("nsfwplus45.py", "classing45.py", "actkeys45.py", "segments45.py", "rplong_mean.py", "attempts45.py",
                "fixrows45.py", "chains.py", "packs45.py", "headline.py", "enginepacks45.py",
                # stamp-2 fix pass (review finding 6): every module that computes points, margins, separation or a
                # mark result (report.py and display45.py stay out: see docs/SCORING.md)
                "scoring45.py", "tiers.py", "scale.py", "agree.py", "lineage.py", "assistant45.py",
                "agree_marks.py", "rplong_marks.py", "marksinput45.py", "resolution45.py",
                # NSFW+ packs (stamp-2 item nw): the pack scorer and the predicate that keeps pack rows out of standard
                "nsfwpack45.py",
                # one local judge classes the adult page (Part 1, 2026-11-21): the one-vote verdict rules
                "judge45.py", "flags45.py")
#: data files the published numbers depend on, relative to the repository root (the profile holds the tree and its
#: weights)
SCORER_DATA = ("profiles/bench45.yaml", "calibration/MARKS-INDEX-45.md",
               # the mark scripts decide the stored results that hold segments (repository-relative)
               "scripts/check_pack_marks45.py", "scripts/check_marks45.py", "scripts/check_rplong_marks45.py",
               "scripts/check_judge_marks.py", "scripts/check_classing_marks.py", "scripts/check_assistant_marks.py",
               "scripts/resolution45.py", "scripts/check_nsfw_pack_marks45.py",
               # the attest gate decides the attestation and wiring marks of the NSFW+ packs (ladder.py stays out:
               # tests/test_gen_gaps_20261120.py forbids ladder-named stamp inputs)
               "scripts/attest_check.py")


@functools.lru_cache(maxsize=1)
def scorer_stamp() -> str:
    """sha256 (16 hex) over the report-time scorer files (:data:`SCORER_FILES`) and ``calibration/MARKS-INDEX-45.md``.
    ``report.json`` carries it per model as ``scorer_stamp``: two reports of the same stored rows that print
    different numbers differ here, and a published number can be traced to the scorers that made it."""
    here = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for f in SCORER_FILES:
        h.update(f.encode() + b"\0" + (here / f).read_bytes() + b"\0")
    for f in SCORER_DATA:
        p_ = here.parent / f
        h.update(Path(f).name.encode() + b"\0" + (p_.read_bytes() if p_.is_file() else b"") + b"\0")
    return h.hexdigest()[:16]


def stamp_line(current: str, published: str | None = None) -> str:
    """The board footer's stamp line: "scored with <stamp>"; when the published report this board replaces carried
    another stamp, "re-scored under <stamp> (published under <old>)"."""
    if published and published != current:
        return f"re-scored under {current} (published under {published})"
    return f"scored with {current}"


def published_stamps(path) -> dict:
    """{model label: scorer_stamp} from a published report.json (``results/public/report.json``); {} when absent."""
    import json
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    ms = d.get("models") if isinstance(d, dict) else None
    return {l: m.get("scorer_stamp") for l, m in (ms or {}).items()
            if isinstance(m, dict) and m.get("scorer_stamp")}
