"""Chat packs under 4.5 (realign45-packs-chat; wired at integration 2026-11-20): the judged pack scenes scored like
the standard long scenes.

New module, nothing here edits a grading module (grader-stamp guard; ``rplong_mean`` and ``agree`` stay byte for
byte). Since the integration it is in ``segments45.SCORER_FILES`` (it decides printed pack numbers), and the report
(``report._apply_segments45``) and the run side (``report.ladder_progress``) call it for the parts ``chat/pk_*``.

Why it exists. ``rplong_mean`` / ``agree`` recognise a judged long scene only by a hard-coded case-id list
(``agree.LONG_CASES``, ``rplong_mean.SCENES``), and the mark that validates the mean-of-two
(``agree_marks``) requires every id of that list. A judged set under a Chat part therefore never got a second
reader and its unlock/points came from the judge alone. This module gives the pack scenes their own registry and
applies the SAME rule: scene score = mean(judge fact rate, pinned second reader's fact rate), the reader store
and the reader fit rules are rplong_mean's, a scene no fitting reader has read is pending, never guessed.

Pack rules (docs/SCORING.md "Ladder packs"): a pack belongs to a segment's material, opens when the segment's
standard share is >= ``ladder.unlock`` (0.9) and every lower pack level is done, is worth up to ``points`` (1),
and is printed only in the Pack points column (never Code, Chat or Overall). A judged pack prints only when its
marks (calibration/pack-marks-chat-45-addendum-20261120.json: P1 reader agreement, P2 known-order pair, P3 not
maxed) hold. The marks are evaluated by scripts/check_pack_marks45.py and stored in ``marks45.json`` (block
``chat_packs``); :func:`board_packs` reads that block and scores a judged level only when P1, P2 and P3 are each a
stored pass (fail closed: a missing, failed or not-evaluable mark leaves the pack pending, P3 failed = MAXED).
"""
from __future__ import annotations

from pathlib import Path

from . import agree as AG
from . import rplong_mean as RM

#: pack scene id -> (segment, pack set key, level). Judged sets only; the generated levels are code-checked.
PACK_SCENES = {
    "LRV1-pilots-mess": ("character", "chat/pk_character", 1),
    "LRV3-hollis-van": ("character", "chat/pk_character", 1),
    "LCC1-quiet-hours": ("remembers", "chat/pk_remembers", 1),
    "LCC2-last-boxes": ("remembers", "chat/pk_remembers", 1),
    "LCC3-left-luggage": ("remembers", "chat/pk_remembers", 1),
    "LWR1-skerrow-bible": ("cowrites", "chat/pk_cowrites", 1),
    "LWR2-neap-seam": ("cowrites", "chat/pk_cowrites", 1),
    "LWR3-twelve-days": ("cowrites", "chat/pk_cowrites", 1),
}
#: generated, code-checked levels (no judge, no reader): segment -> (pack set key, level)
CODE_LEVELS = {"character": ("chat/pk_character", 2), "remembers": ("chat/pk_remembers", 2),
               "cowrites": ("chat/pk_cowrites", 2)}
SEGMENTS = ("character", "remembers", "cowrites")
UNLOCK = 0.9
#: the agree part name that routes a folder's pack scenes to :func:`run_second_reader`
PACKS_PART = "chat/packs45"
#: mark ids of the stored ``chat_packs`` block (P1 and P4 at the top, P2 and P3 per segment under ``segments``)
P1_KEY, P2_KEY, P3_KEY, P4_KEY = ("P1_reader_agreement", "P2_known_order", "P3_not_maxed", "P4_key_audit_code_levels")
#: P3: a level whose best run on file reaches this share is MAXED
P3_MAX = 0.97


def pack_units(rows: list[dict]) -> dict[str, dict]:
    """{unit id: newest JUDGED row} of the pack scenes, per (run, case, repeat), as ``agree.long_units``."""
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("rubric") != "rp_deep" or r.get("case_id") not in PACK_SCENES:
            continue
        j = r.get("judge")
        if not isinstance(j, dict) or not (j.get("elements") or {}).get("results"):
            continue
        out[f"{r.get('bench_run_id')}|{r['case_id']}|{r.get('repeat') or 1}"] = r
    return out


def case_map() -> dict[str, dict]:
    from . import config
    return {c["id"]: c for c in config.load_cases() if c.get("id") in PACK_SCENES}


def run_second_reader(rows, reader, *, jc, results, label, cases=None, graded_family=None,
                      judge_family=RM.JUDGE_FAMILY) -> list[dict]:
    """``rplong_mean.run_second_reader`` for the pack scenes (same reader store, same prompts, same fit check; a
    hosted reader is refused before any call, owner 2026-11-21)."""
    if reader.hosted:
        from .judge45 import HostedGrader
        raise HostedGrader(f"Chat pack-scene second reader: {reader.id} is hosted; MakoBench 4.5 grades with one "
                           "local judge only (no hosted grader or checker). Nothing was called.")
    units = pack_units(rows)
    gf = graded_family or AG.graded_family(label, next(iter(units.values()), None))
    bad = RM.check_fit(reader, gf, judge_family)
    if bad:
        raise RM.NoFittingReader(bad)
    cases = case_map() if cases is None else cases
    have = RM.load_store(results, label)
    out, new = [], []
    for uid, row in sorted(units.items()):
        psha = RM.prompt_sha_of(row)
        k = (uid, reader.key, psha, AG.row_sha(row))
        if k in have:
            out.append(have[k])
            continue
        v = AG.regrade_long(jc, row, reader.thinking)
        if v.get("judge_failed") or not (v.get("elements") or {}).get("results"):
            continue
        rec = AG.long_record(label, uid, row, v, cases.get(row["case_id"]), reader.key)
        rec["graded_family"] = gf
        rec["reader_stamp"] = reader.stamp(psha)
        new.append(rec)
        out.append(rec)
    if new:
        RM.append_store(results, label, new)
    return out


def scene_scores(rows, records, *, label, cases=None, graded_family=None, judge_family=RM.JUDGE_FAMILY,
                 reader_for=None, readers=None) -> dict:
    """{unit id: {case, segment, judge, reader, mean, status, reason, reader_stamp}} of the pack scenes: the same
    rule as ``rplong_mean.scene_scores`` (pinned reader, record must carry that reader's id, family and the row's
    current rubric sha; otherwise pending with the reason)."""
    cases = case_map() if cases is None else cases
    units = pack_units(rows)
    gf = graded_family or AG.graded_family(label, next(iter(units.values()), None))
    pin, pin_why = reader_for, None
    if pin is not None:
        pin_why = RM.check_fit(pin, gf, judge_family)
        pin = None if pin_why else pin
    else:
        try:
            pin = RM.pinned_reader(gf, readers if readers is not None else RM.load_readers(), True)
        except RM.NoFittingReader as e:
            pin_why = str(e)
    by_unit: dict[str, list[dict]] = {}
    for r in records:
        if r.get("part", AG.LONG_PART) == AG.LONG_PART and r.get("model", label) == label:
            by_unit.setdefault(r["unit"], []).append(r)
    out = {}
    for uid, row in sorted(units.items()):
        case = row["case_id"]
        jrate = AG.long_score(row, row.get("judge") or {}, cases.get(case))
        cur = RM.prompt_sha_of(row)
        cand = [r for r in by_unit.get(uid, []) if r.get("row_sha") in (None, AG.row_sha(row))]
        good, why = None, None
        if pin is None:
            why = pin_why or "no reader named for this model's family"
        else:
            mine = [r for r in cand
                    if RM.canonical_id((r.get("reader_stamp") or {}).get("model")) == pin.id
                    and (r.get("reader_stamp") or {}).get("family") == pin.family
                    and cur and (r.get("reader_stamp") or {}).get("prompt_sha") == cur
                    and r.get("score_reader") is not None]
            if mine:
                good = mine[-1]
            elif cand:
                why = f"no record from the pinned reader {pin.id} under this rubric"
            else:
                why = "not read by a second reader"
        e = {"case": case, "segment": PACK_SCENES[case][0], "judge": jrate, "reader": None, "mean": None,
             "status": "pending", "reason": why, "reader_stamp": None}
        if good is not None and jrate is not None:
            e.update(reader=good["score_reader"], mean=RM.scene_score(jrate, good["score_reader"]),
                     status="ok", reason=None, reader_stamp=good.get("reader_stamp"))
        elif jrate is None:
            e["reason"] = "judge verdict not scorable"
        out[uid] = e
    return out


def judge_scores(rows, records=None, *, label, cases=None) -> dict:
    """The pack scenes scored by the judge alone (11b, lead 2026-11-21): ``rplong_mean.judge_scores`` over the pack
    units, segment from :data:`PACK_SCENES`. Stored second-reader votes are ignored."""
    cases = case_map() if cases is None else cases
    out = RM.judge_scores(rows, None, label=label, cases=cases, units=pack_units(rows))
    for u in out.values():
        u["segment"] = PACK_SCENES.get(u["case"], (None,))[0]
    return out


def scene_means(scores: dict, segment: str) -> tuple[dict[str, float] | None, str | None]:
    """{case: mean over its repeats of the scene mean-of-two} of a segment's judged scenes, or (None, reason) when
    any scene is missing or pending (never a partial set)."""
    want = [c for c, v in PACK_SCENES.items() if v[0] == segment]
    per: dict[str, list[float]] = {}
    why = None
    for u in scores.values():
        if u["segment"] != segment:
            continue
        if u["status"] == "ok":
            per.setdefault(u["case"], []).append(u["mean"])
        else:
            why = why or f"{u['case']}: {u['reason']}"
    miss = [c for c in want if c not in per]
    if miss or why:
        return None, why or "scene not run: " + ", ".join(miss)
    return {c: sum(v) / len(v) for c, v in per.items()}, None


def judged_share(scores: dict, segment: str) -> tuple[float | None, str | None]:
    """The judged level's share for a segment: mean over its scenes of each scene's mean over repeats. None with a
    reason when any scene is missing or pending (a pack never prints a partial number)."""
    means, why = scene_means(scores, segment)
    if means is None:
        return None, why
    return sum(means.values()) / len(means), None


def unlock_of(prof: dict | None) -> float:
    """The ONE unlock threshold (share of the part): the profile's ``ladder.unlock`` (0.9, the owner 2026-11-14)."""
    from . import ladder as _ladder
    return _ladder.unlock_share(prof) if prof else UNLOCK


def segment_unlock(segment_points: float | None, segment_max: float, unlock: float = UNLOCK,
                   range_points: float = 0.0) -> bool:
    """A pack opens when the TOP of the model's own range of the segment's STANDARD points (points + range half-width,
    never pack points) reaches ``unlock`` of the segment's maximum (DECISIONS amendment q, 2026-11-21)."""
    return (segment_points is not None and segment_max > 0
            and (segment_points + max(0.0, float(range_points or 0.0))) / segment_max >= unlock - 1e-12)


def pack_points(segment: str, std_points: float | None, std_max: float, judged: float | None,
                code: float | None, *, marks_pass: bool = True, unlock: float = UNLOCK,
                range_points: float = 0.0) -> dict:
    """{state, points, level shares, reason} for one segment's pack. Level 1 (judged) needs the unlock; level 2
    (code) needs level 1 >= ``unlock`` too. Not judged yet = pending, 0 points. Max 1 point per level, never added to
    the segment's standard points. ``marks_pass`` is kept for callers: a pack mark never gates (INTENT-45)."""
    if not segment_unlock(std_points, std_max, unlock, range_points):
        return {"state": "locked", "points": 0.0, "reason": "segment standard share (top of its range) below the "
                                                             f"{unlock * 100:.0f}% unlock"}
    if judged is None:
        return {"state": "pending", "points": 0.0, "reason": "judged level not judged yet"}
    pts = min(1.0, max(0.0, judged))
    if judged >= unlock and code is not None:
        pts += min(1.0, max(0.0, code))
    return {"state": "scored", "points": pts, "judged": judged, "code": code if judged >= unlock else None}


# ------------------------------------------------------------------ integration (2026-11-20)
#: the profile's pack parts: segment -> chat part key (``pack_segment`` in profiles/bench45.yaml)
PART_OF = {"character": "pk_character", "remembers": "pk_remembers", "cowrites": "pk_cowrites"}
P1_MIN_UNITS = 8
P1_MAX_TERM = 0.05


def segment_share(tb_chat: dict | None, segment: str) -> float | None:
    """The segment's STANDARD share for the gate: points / max of a complete, unheld segment, else None (a held or
    partly measured segment opens nothing, as a part pending validation opens nothing)."""
    g = ((tb_chat or {}).get("segments") or {}).get(segment) or {}
    if not g or g.get("points") is None or not g.get("complete") or not g.get("max"):
        return None
    return float(g["points"]) / float(g["max"])


def p1_reader_agreement(scores: dict) -> dict:
    """P1 for ONE model: the judge term (``rplong_mean.judge_term`` over judge - reader of its read pack scenes) and
    whether it is evaluable (>= :data:`P1_MIN_UNITS` read units). The mark itself takes the largest term over the
    models measured (the caller's job)."""
    diffs = [u["judge"] - u["reader"] for u in scores.values()
             if u.get("status") == "ok" and u.get("judge") is not None and u.get("reader") is not None]
    if len(diffs) < P1_MIN_UNITS:
        return {"state": "not_evaluable", "n": len(diffs), "term": None}
    t = RM.judge_term(diffs)
    return {"state": "measured", "n": len(diffs), "term": t}


def _mark(marks: dict | None, *path) -> dict:
    """One stored mark result {status, reason}; anything absent or unreadable is ``missing`` (never a pass)."""
    cur = marks
    for k in path:
        cur = cur.get(k) if isinstance(cur, dict) else None
    if not isinstance(cur, dict) or cur.get("status") not in ("pass", "fail", "not_evaluable"):
        return {"status": "missing", "reason": ""}
    return cur


def _why(name: str, m: dict) -> str:
    return str(m.get("reason") or f"{name} {'not measured' if m['status'] == 'missing' else m['status']}")


def _judged_range(scores: dict, segment: str) -> float | None:
    """The 90% half-width of a segment's judged level from its own scene repeats (accuracy45): each scene's mean over
    its repeats, w = 1 / scenes; None when no scene has two repeats."""
    from . import accuracy45 as _A
    per: dict[str, list[float]] = {}
    for u in scores.values():
        if u.get("segment") == segment and u.get("status") == "ok":
            per.setdefault(u["case"], []).append(float(u["mean"]))
    if not per:
        return None
    return _A.repeat_half([(1.0 / len(per), v) for v in per.values()])


def board_packs(tb_chat: dict | None, scores: dict, code_shares: dict, unlock: float = UNLOCK,
                marks: dict | None = None, ranges: dict | None = None) -> dict:
    """{segment: pack_points(...) + gate share + range + notes} for one model's board (INTENT-45: a pack unlocks at
    ``unlock`` of its part on the TOP of the model's own range (``ranges`` {segment: half-width points}), scores,
    prints its own number and range; "at least" when the model maxes it; the pack marks P1-P4 are evaluated where
    stored and print as notes only, never a gate). ``code_shares`` = {segment: share of the generated level 2}."""
    p1 = p1_reader_agreement(scores)
    out = {}
    for seg in SEGMENTS:
        sh = segment_share(tb_chat, seg)
        g = ((tb_chat or {}).get("segments") or {}).get(seg) or {}
        mx = float(g.get("max") or 0)
        judged, why = judged_share(scores, seg)
        std = None if sh is None else sh * mx
        ms = {"P1": _mark(marks, P1_KEY), "P2": _mark(marks, "segments", seg, P2_KEY),
              "P3": _mark(marks, "segments", seg, P3_KEY), "P4": _mark(marks, P4_KEY)}
        rp = float((ranges or {}).get(seg) or 0.0)
        res = pack_points(seg, std, mx, judged, code_shares.get(seg), unlock=unlock, range_points=rp)
        # P1 (second-reader agreement) and P2 (known order) served readers and separation: never printed
        notes = [f"note: {_why(k, m)}" for k, m in ms.items()
                 if k not in ("P1", "P2") and m["status"] not in ("pass", "missing")]
        jr = _judged_range(scores, seg) if res["state"] == "scored" else None
        out[seg] = {**res, "gate_share": None if sh is None else round(sh, 4),
                    "gate_top": None if sh is None or not mx else round(sh + rp / mx, 4), "part": PART_OF[seg],
                    "judged_reason": why, "p1": p1, "marks": {k: m["status"] for k, m in ms.items()},
                    "notes": notes, "range": None if jr is None else round(jr, 4),
                    "at_least": res["state"] == "scored" and judged is not None and judged >= 1.0 - 1e-9}
    return out
