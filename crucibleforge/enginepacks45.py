"""Engine ladder packs on the top of the model's own range (step 2 of 4.5, 2026-11-21).

The NSFW+ and Chat packs open when the TOP of the model's own range of the gating standard share reaches the unlock
share (``nsfwpack45.board_packs``, ``packs45.segment_unlock``). The engine's ladder packs (Code parts and the
Everyday part) gated on the bare standard share. This module applies the same rule to them, on both sides:

* board: :func:`engine_delta` re-states each engine part's ladder sets with ``gate + lift`` and returns the points
  the lift adds (the engine already counted the sets its bare gate opened);
* run side: :func:`run_gates` adds the same lift to the gate ``ladder_progress`` hands to ``ladder.due``.

``lift`` = the part's own repeat range as a share where this run measured one, else its segment's range
(``accuracy45`` half / segment max). The Fixes pack (part ``work/c3_fix``, max 0, no standard rows of its own)
gates on the Fixes SEGMENT's standard share on the top of its range (:func:`fixes_pack`). Nothing here changes a
standard number; pack points stay in the section's pack column.
"""
from __future__ import annotations

from . import ladder as _ladder

#: the Fixes pack part and the segment that gates it
FIXES_PACK_PART = "work/c3_fix"
FIXES_SEGMENT = "code/fixes"
_SEC_OF = {"code": "work", "chat": "chat"}


def part_segments(tree: dict | None) -> dict[str, str]:
    """{"work/c2_trace": "code/reads", ...}: every engine part named in the score tree -> its segment, plus the
    Fixes pack part. Long scenes, Assistant basics and fix-task cases are not engine parts; Chat pack parts keep
    packs45's own gate."""
    out: dict[str, str] = {}
    for sec, cat in _SEC_OF.items():
        for seg, spec in ((tree or {}).get(sec) or {}).items():
            if not isinstance(spec, dict):
                continue
            for part, w in (spec.get("parts") or {}).items():
                if isinstance(w, dict) or part in ("assistant", "rplong"):
                    continue
                out[f"{cat}/{part}"] = f"{sec}/{seg}"
    out[FIXES_PACK_PART] = FIXES_SEGMENT
    return out


def _seq(det: dict) -> tuple[list[dict], dict]:
    sets = det.get("sets") or []
    return ([{"n": s["n"], "level": s["level"]} for s in sets],
            {s["n"]: s.get("share") for s in sets})


def pack_points(det: dict, gate: float | None, unlock: float) -> float:
    """Points of a part's ladder sets when its gate reads ``gate``: the share x points of every set the levels
    open in order (``ladder.states``)."""
    seq, shares = _seq(det)
    if not seq:
        return 0.0
    st = _ladder.states(gate, shares, seq, unlock)
    pts = {s["n"]: float(s.get("points", 1.0)) for s in det.get("sets") or []}
    return round(sum((shares.get(n) or 0.0) * pts.get(n, 1.0) for n, x in st.items() if x == "scored"), 4)


def _seg_lift(acc_segments: dict | None, seg: str) -> float:
    b = (acc_segments or {}).get(seg) or {}
    half, mx = b.get("half"), float(b.get("max") or 0.0)
    return float(half) / mx if half is not None and mx > 0 else 0.0


def lift_of(key: str, seg: str, acc_segments: dict | None, part_ranges: dict | None) -> float:
    """The part's own measured repeat range (a share) where there is one, else its segment's range as a share."""
    if part_ranges and part_ranges.get(key) is not None:
        return float(part_ranges[key])
    return _seg_lift(acc_segments, seg)


def engine_delta(card_ladder: dict | None, tree: dict | None, acc_segments: dict | None,
                 part_ranges: dict | None, unlock: float) -> dict:
    """{points: the pack points the range adds over the engine's bare gate, parts: {key: {gate, lift, gate_top,
    points, delta}}}. A part with no gate (standard rows missing, pending validation) or one a route opened is left
    as the engine scored it."""
    segs = part_segments(tree)
    parts: dict = {}
    total = 0.0
    for cat, by in (card_ladder or {}).items():
        for pk, det in (by or {}).items():
            key = f"{cat}/{pk}"
            if key not in segs or key == FIXES_PACK_PART or det.get("opened") is not None:
                continue
            gate = det.get("unlock_share")
            if gate is None or not det.get("sets"):
                continue
            lift = lift_of(key, segs[key], acc_segments, part_ranges)
            top = float(gate) + lift
            new, old = pack_points(det, top, unlock), pack_points(det, float(gate), unlock)
            parts[key] = {"gate": round(float(gate), 4), "lift": round(lift, 4), "gate_top": round(top, 4),
                          "points": new, "delta": round(new - old, 4)}
            total += new - old
    return {"points": round(total, 4), "parts": parts}


def fixes_gate(tb_code: dict | None, acc_segments: dict | None) -> float | None:
    """The top of the Fixes segment's range as a share, or None when Fixes is held or partly measured."""
    g = ((tb_code or {}).get("segments") or {}).get("fixes") or {}
    if g.get("points") is None or not g.get("complete") or not g.get("max"):
        return None
    return (float(g["points"]) + _seg_lift(acc_segments, FIXES_SEGMENT) * float(g["max"])) / float(g["max"])


def fixes_pack(det: dict | None, tb_code: dict | None, acc_segments: dict | None, unlock: float) -> dict:
    """The Fixes pack for one model: {state, points, gate_top, sets}. Locked while the top of the Fixes segment's
    range is below ``unlock`` (or Fixes has no complete number); otherwise its levels open in order."""
    top = fixes_gate(tb_code, acc_segments)
    det = det or {}
    seq, shares = _seq(det)
    st = _ladder.states(top, shares, seq, unlock) if seq else {}
    pts = pack_points(det, top, unlock)
    open_ = top is not None and top >= unlock
    state = "locked" if not open_ else "scored" if any(v == "scored" for v in st.values()) else "pending"
    return {"state": state, "points": pts if open_ else 0.0,
            "gate_top": None if top is None else round(top, 4), "sets": st}


def run_gates(out: dict, tb: dict | None, acc_segments: dict | None, tree: dict | None) -> dict:
    """The run side (``report.ladder_progress``): each engine part's gate gains its segment's range, and the Fixes
    pack's gate is the top of the Fixes segment's range. Chat pack parts are left to packs45. In place."""
    segs = part_segments(tree)
    for key, row in out.items():
        if key not in segs or row.get("opened") is not None:
            continue
        if key == FIXES_PACK_PART:
            row["std"] = fixes_gate((tb or {}).get("code"), acc_segments)
        elif row.get("std") is not None:
            row["std"] = float(row["std"]) + _seg_lift(acc_segments, segs[key])
    return out
