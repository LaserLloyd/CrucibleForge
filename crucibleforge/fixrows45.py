"""Fix-task rows for the "Fixes and changes existing code" segment (4.5 realignment, report time).

The bug-fix checker (``fixcheck.py`` + ``fixspecs.py``) sits outside every ``grader_stamp`` closure on purpose
(docs/FIXCHECK.md), so its rows carry their own stamp: a row counts only when it holds a ``fixcheck`` block
written by :func:`stamp_verdict` whose ``stamp`` equals :func:`checker_stamp` of the code that reads it. A row of a
fix case graded only by the old code-bank grader (forgeable, EXP-20261114-30) never counts; nor does a row whose
checker changed since (it is reported, to be re-graded).

Row contract (the run lane writes it): ``row["fixcheck"] = stamp_verdict(fixcheck.grade_answer(...))`` for the
one-shot cases, ``stamp_verdict(fixcheck.grade_agent(env))`` for CB-F1.
"""
from __future__ import annotations

import functools
import hashlib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
#: the checker and every helper whose behaviour decides a verdict: the answer-tree builder and group scorer
#: (codebank), the hidden-file builder (gen/_patchbase) and the sandbox arguments (graders)
_FILES = ("fixcheck.py", "fixspecs.py", "codebank.py", "gen/_patchbase.py", "graders.py")


@functools.lru_cache(maxsize=1)
def checker_stamp() -> str:
    """sha256 (12 hex) over the checker's files (_FILES): a change to any re-grades every fix row (bug-hunt rows
    cannot be re-graded: their final tree is not stored, so they go pending until re-generated)."""
    h = hashlib.sha256()
    for f in _FILES:
        h.update(f.encode() + b"\0" + (_HERE / f).read_bytes() + b"\0")
    return h.hexdigest()[:12]


#: the ``fixcheck`` block of a row whose verdict could not be computed here (no working sandbox): it never counts
UNAVAILABLE = {"unavailable": True}
NO_SANDBOX_REASON = "pending: fix rows not graded: no sandbox"


def stamp_verdict(verdict: dict) -> dict:
    """The ``fixcheck`` block a fix row stores: the checker's score and grade plus its stamp."""
    return {"score": float(verdict["score"]), "grade": verdict.get("grade"), "checker": verdict.get("checker"),
            "stamp": checker_stamp()}


#: generator families whose single-turn replies the bug-fix checker grades (the run lane stamps them)
FIX_FAMILIES = frozenset({"patchfix", "patchmini", "patchmod", "patchhard"})   # patchmini: step 2 (2026-11-21)
#: D10a (chains.FULL_BUDGET_SHARE): a reply that ended on the length limit having used this share of the budget sent
FULL_BUDGET_SHARE = 0.98


def is_fix_case(case: dict) -> bool:
    """True for the units of the Fixes segment: the CB-F1 agent case and a generated patch-family case (a case that
    is a unit of a chain keeps its chain grader)."""
    if (case.get("generator") or {}).get("type") in FIX_FAMILIES and case.get("grader_config") and not case.get("chain"):
        return True
    return str(case.get("id") or "").startswith("CB-F1-") and bool(case.get("env"))


def reply_verdict(case: dict, reply: str, *, finish_reason: str | None, completion_tokens: int | None,
                  sent: int | None, blank: bool = False) -> dict | None:
    """The ``fixcheck`` block of a one-shot fix row; ``{"unavailable": True}`` when the checker cannot run here (no
    bwrap): such a row never counts and the segment says "fix rows not graded: no sandbox". A reply cut at the full budget sent scores 0 (D10a, ``cut: true``); an empty
    reply scores 0; anything else is graded as delivered by :func:`fixcheck.grade_answer`."""
    from . import codebank as _cb
    from . import fixcheck
    from .graders import SandboxUnavailable
    cut_full = bool(finish_reason == "length" and sent and completion_tokens is not None
                    and float(completion_tokens) >= FULL_BUDGET_SHARE * float(sent))
    if blank or cut_full or not (reply or "").strip():
        return {**stamp_verdict({"score": 0.0, "grade": "fail", "checker": "fixcheck-host-compared-1"}),
                "cut": cut_full, "empty": bool(blank or not (reply or "").strip())}
    try:
        v = fixcheck.grade_answer(reply, case["grader_config"], case_id=case.get("id"), finish_reason=finish_reason)
    except (SandboxUnavailable, _cb.Unsupported):
        return dict(UNAVAILABLE)
    return {**stamp_verdict(v), "cut": False, "short_cut": finish_reason == "length"}


def agent_verdict(env, *, any_cut: bool) -> dict | None:
    """The ``fixcheck`` block of a finished CB-F1 agent session (its final tree, scope element included)."""
    from . import codebank as _cb
    from . import fixcheck
    from .graders import SandboxUnavailable
    try:
        v = fixcheck.grade_agent(env)
    except (SandboxUnavailable, _cb.Unsupported):
        return dict(UNAVAILABLE)
    return {**stamp_verdict(v), "cut": bool(any_cut)}


def case_scores(rows: list[dict], cases: list[str]) -> dict:
    """``{"cases": {case: {"scores": [score per repeat, repeat order], "reason": str | None, "stale": n,
    "old_grader": n}}, "stamp": checker_stamp()}``. The scored row per (case, repeat) is picked by the first-reply rule (attempts45); a missing input is a
    reason, never 0."""
    want = set(cases)
    cur = checker_stamp()
    # retry fairness (attempts45): the scored attempt of a unit is its first reply; a later attempt counts only after
    # an infrastructure failure. The same selection the board rows get, so it is a no-op on rows already selected.
    from .report import _board_rows
    rows = _board_rows([r for r in rows if r.get("case_id") in want])
    newest: dict = {}
    stale: dict = {}
    old: dict = {}
    nosb: dict = {}
    for r in rows:
        c = r.get("case_id")
        if c not in want:
            continue
        if r.get("grade") == "error":
            continue                  # an infrastructure failure: no verdict, never an old-grader row
        fc = r.get("fixcheck")
        if isinstance(fc, dict) and fc.get("unavailable"):
            nosb[c] = nosb.get(c, 0) + 1          # no sandbox here: not graded, never an old-grader row
            continue
        if not isinstance(fc, dict):
            old[c] = old.get(c, 0) + 1
            continue
        if fc.get("stamp") != cur:
            stale[c] = stale.get(c, 0) + 1
            continue
        k = (c, int(r.get("repeat") or 1))
        if k not in newest or str(r.get("ts") or "") >= str(newest[k].get("ts") or ""):
            newest[k] = r
    out = {}
    for c in cases:
        reps = sorted(rep for (cc, rep) in newest if cc == c)
        scores = [float(newest[(c, rep)]["fixcheck"]["score"]) for rep in reps]
        if scores:
            reason = None
        elif nosb.get(c):
            reason = NO_SANDBOX_REASON
        elif stale.get(c):
            reason = f"pending: {stale[c]} fix row(s) graded by an older checker: re-grade"
        else:
            reason = "pending: no fix rows yet"
        out[c] = {"scores": scores, "reason": reason, "stale": stale.get(c, 0), "old_grader": old.get(c, 0),
                  **({"no_sandbox": nosb[c]} if nosb.get(c) else {})}
    return {"cases": out, "stamp": cur}
