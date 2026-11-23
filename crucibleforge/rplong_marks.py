"""The pre-registered marks for the eight long chat scenes under mean-of-two scoring (SPEC-REALIGN-45 1.2, 3, 4.5).

The marks file is the promoted-unchanged draft (``calibration/judge-marks-45.1-draft.json``); its sha256 is pinned
in ``calibration/MARKS-INDEX-45.md`` and re-checked on every load. New module: the frozen 4.5.0 marks code
(agree_marks) is reused, not edited. The one difference is the judge term: under ``mean_of_two_readers`` the score
a scene carries is the mean of two readers, so the single-reader judge term of agree_marks halves in quadrature
(divides by sqrt 2). Limit unchanged (5% of the part).

Exit meaning for the script: 0 pass, 1 fail, 2 not measured (no records)."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from . import agree as AG
from . import agree_marks as AM
from . import rplong_mean as RM

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "calibration" / "judge-marks-45.1-draft.json"
INDEX = ROOT / "calibration" / "MARKS-INDEX-45.md"
PART = AG.LONG_PART
GROUPS = {"character": ("RV1-three-at-the-table", "RV2-skarren-hut"),
          "remembers": ("CC3-tea-hut", "CC4-second-pitch", "RD1-marisol", "RD4-kettle-saddle"),
          "cowrites": ("WR1-ninth-string", "WR2-red-thread-key")}
RESULT_KEY = "chat_rplong"


class MarksHashMismatch(RuntimeError):
    pass


def indexed_sha(name: str = "calibration/judge-marks-45.1-draft.json") -> str | None:
    m = re.search(r"`" + re.escape(name) + r"`\s*\|\s*`([0-9a-f]{64})`", INDEX.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def load_marks(path: Path | str | None = None) -> dict:
    """The draft marks, refused when their bytes do not hash to the index row."""
    p = Path(path or DRAFT)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    if p == DRAFT and sha != indexed_sha():
        raise MarksHashMismatch(f"{p.name} sha {sha[:12]} is not the indexed one")
    return json.loads(p.read_text(encoding="utf-8"))


def _group_of(unit: str) -> str | None:
    case = unit.split("|")[1] if "|" in unit else unit
    return next((g for g, cs in GROUPS.items() if case in cs), None)


def terms(records: list[dict]) -> dict:
    """Judge term (share of the scene score, 0-1, mean of two readers) overall and per scene group, from the
    stored unit scores of both readers. {name: {n, gap, term, single}}; ``gap`` = mean(judge - reader)."""
    out = {}
    sets = {"all": records, **{g: [r for r in records if _group_of(r["unit"]) == g] for g in GROUPS}}
    for name, rs in sets.items():
        d = [r["score_judge"] - r["score_reader"] for r in rs
             if r.get("score_judge") is not None and r.get("score_reader") is not None]
        if not d:
            out[name] = {"n": 0, "gap": None, "term": None, "single": None}
            continue
        out[name] = {"n": len(d), "gap": sum(d) / len(d), "term": AM.Z90 * RM.judge_term(d, 2),
                     "single": AM.Z90 * RM.judge_term(d, 1)}
    return out


def check(records: list[dict], marks: dict, *, rows_by_model: dict | None = None, cases: dict | None = None,
          margins: dict | None = None, scores: dict | None = None) -> dict:
    """The part's result: {pass, measured, reason, fails, judge_term_share, groups, informational, ...}.
    ``records`` = reader records of the eight scenes (stored judge + reader scores)."""
    mk = AM.marks_for(marks, PART)
    primary = marks.get("primary") or {}
    records = AG.standard_long(records)                 # pack scenes share the part id; they are not this mark's
    if not records:
        return {"pass": False, "measured": False, "reason": "agreement with the second reader not measured",
                "fails": ["not measured"], "groups": {}}
    s = AG.summarize(records).get(PART) or {}
    t = terms(records)
    cap = float(primary.get("max_judge_term_share", 0.05))
    # agree_marks' judge-term line is the single-reader one: replace it by the mean-of-two term
    fails = [f for f in AM.check_primary(PART, s, mk, primary, margins) if not f.startswith("judge term")]
    jt = t["all"]["term"]
    if jt is None:
        fails.insert(0, "judge term not measurable (no unit scored by both readers)")
    elif jt > cap:
        fails.insert(0, f"judge term {100 * jt:.1f}% of the part > {100 * cap:.0f}% (mean of two readers)")
    d1 = mk.get("d1") or {}
    rb = rows_by_model or {}
    if rb:
        if d1.get("a_judged_stamped"):
            fails += AM.d1_a(rb)
        if d1.get("b_no_unexplained_ooc_zero"):
            fails += AM.d1_b(rb, cases or {})
        if d1.get("e_all_cases"):
            fails += AM.d1_e(rb)
    if d1.get("d_known_order"):
        sc = (scores or {}).get(PART) or {m: v[0] for m, v in (s.get("model_means") or {}).items()}
        fails += AM.d1_d(sc, d1["d_known_order"])
    return {"pass": not fails, "measured": True, "reason": "all marks pass" if not fails else fails[0], "fails": fails,
            "judge_term_share": jt, "judge_term_single_reader": t["all"]["single"], "limit": cap, "groups": t,
            "informational": AM.informational(s, mk), "n_units_by_model": s.get("n_units_by_model") or {},
            "marks": DRAFT.name, "marks_sha256": hashlib.sha256(DRAFT.read_bytes()).hexdigest(),
            "score": "mean_of_two_readers"}


def write_result(results: Path | str, res: dict) -> Path:
    """Merge the part's result into ``<results>/marks45.json`` (the file the board reads)."""
    p = Path(results) / "marks45.json"
    body = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    body[RESULT_KEY] = res
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body, indent=1, default=list), encoding="utf-8")
    return p


def exit_code(res: dict) -> int:
    return 2 if not res.get("measured", True) else (0 if res["pass"] else 1)
