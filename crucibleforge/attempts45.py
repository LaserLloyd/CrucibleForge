"""Retry fairness at report time (4.5 realignment, lead rule written before any launch row was read).

The scored attempt of a unit is the FIRST attempt that produced a model reply (finish stop, length, or a content
filter on a refusal page; a reply cut at the full budget scores per D10a). A later attempt replaces an earlier one
only when the earlier one was an infrastructure failure: an errored row (transport, HTTP 429/5xx, timeout without a
reply, lease loss, ctx_short), a wall-clock timeout, a skipped row, or a reply that never arrived (a dropped stream,
or a provider content filter off the refusal pages: what ``scoring45`` already treats as MISSING). Later attempts of a
unit whose scored attempt is a reply are ignored and counted.

Why: a run resumed for a second pass (``recover``, a re-draw) used to re-draw cut or errored units and the newest row
won, so a model resumed twice could score higher than the same model run once.

What the stored data can tell apart: every call writes its own row with its own ``run_id``; transcripts are
append-only, so a first attempt is never overwritten (only ``--fresh`` archives them). One unit = (case_id, repeat).
Attempts of a single-call unit = the groups of rows with the same ``run_id``, ordered by (ts, file order).

A multi-turn session is ONE unit (lead decision f, 2026-11-19). Its rows, ordered by (ts, file order), split into
attempts where the turn numbering restarts: a turn number not greater than the one before starts a new attempt. The
runner never continues a session after a failure (a session's rows are written together when it ends; a transport
failure writes one error row with no turn number, which is an attempt of its own, and the next run plays the session
again from turn 1). A row that only continues an earlier attempt after an infrastructure failure at that same turn
is joined to that attempt (its turn takes the first reply, the turns before stay). The scored attempt is the first
one with no infrastructure failure left in it; turns of two conversations are never mixed.
A whole-job re-draw ordered by the operator is a different ``bench_run_id``: ``report._board_rows`` keeps its rule
(the newest complete run counts, with a visible re-run mark); this module only picks inside the kept run.

Row selection only: no grader and no stamp is touched.
"""
from __future__ import annotations

from typing import Iterable

#: profile key (top level) that turns the rule on; absent = off (the 4.1 board stays byte-identical)
RULE_KEY = "attempt_rule"
RULE = "first_reply"


def rule_on(row: dict) -> bool:
    """True when the profile the row was run under asks for the first-reply rule."""
    from .profiles import row_switch
    return str(row_switch(row, RULE_KEY) or "").strip().lower() == RULE


def is_infra(r: dict, refusal_ids=frozenset()) -> bool:
    """One row is an infrastructure failure (no model reply to score): see the module text."""
    from . import scoring45 as S
    if r.get("skipped") or r.get("grade") == "skipped":
        return True
    if S._is_ctx_short(r):
        return True
    if r.get("grade") == "error" or r.get("error_kind") in ("transport", "template", "timeout", "wall"):
        if S._policy_block(r) and (r.get("category") in S.REFUSAL_CATEGORIES or r.get("case_id") in refusal_ids):
            return False
        return True
    if r.get("finish_reason") in ("error", "wall_timeout"):
        return True
    return S._row_empty(r, refusal_ids) in ("empty", "content_filter")


def _numbered(r: dict) -> bool:
    return r.get("turn") not in (None, "final")


def _order(rows: list[dict]) -> list[int]:
    return sorted(range(len(rows)), key=lambda i: (str(rows[i].get("ts") or ""), i))


def session_attempts(rows: Iterable[dict], refusal_ids=frozenset()) -> list[list[dict]]:
    """The rows of ONE multi-turn session split into attempts, oldest first (see the module text): ordered by (ts,
    file order); a numbered row whose turn is not greater than the previous numbered row's starts a new attempt,
    except a row that continues the previous attempt after an infrastructure failure at that same turn (that turn
    has no reply in the attempt yet), which is joined to it. A row with no turn number that is an infrastructure
    failure (the runner's error row of a failed session) is an attempt of its own; a ``final`` row belongs to the
    attempt it follows."""
    rows = list(rows)
    atts: list[list[dict]] = []
    last: int | None = None                  # the last numbered turn of the open attempt (None: no open attempt)
    for i in _order(rows):
        r = rows[i]
        if not _numbered(r):
            if r.get("turn") == "final" and atts and last is not None:
                atts[-1].append(r)
            else:
                atts.append([r])
                last = None
            continue
        t = int(r["turn"])
        if last is not None and t > last:
            atts[-1].append(r)
        elif last is not None and _infra_at(atts[-1], t, refusal_ids):
            atts[-1].append(r)               # a continuation after an infrastructure failure at this turn: joined
        else:
            atts.append([r])
        last = t
    return atts


def _infra_at(att: list[dict], turn: int, refusal_ids) -> bool:
    here = [r for r in att if _numbered(r) and int(r["turn"]) == turn]
    return bool(here) and all(is_infra(r, refusal_ids) for r in here)


def session_rows(att: list[dict], refusal_ids=frozenset()) -> list[dict]:
    """The scored rows of one session attempt: per turn the first row that is not an infrastructure failure (a
    joined continuation), else the newest; ``final`` rows kept; turn order."""
    turns: dict[int, list[dict]] = {}
    for r in att:
        if _numbered(r):
            turns.setdefault(int(r["turn"]), []).append(r)
    out = [first_reply_row(turns[t], refusal_ids) for t in sorted(turns)]
    return out + [r for r in att if r.get("turn") == "final"]


def _attempt_failed(att: list[dict], refusal_ids) -> bool:
    if not any(_numbered(r) for r in att):
        return any(is_infra(r, refusal_ids) for r in att)
    return any(is_infra(r, refusal_ids) for r in session_rows(att, refusal_ids) if _numbered(r))


def attempts(rows: Iterable[dict], refusal_ids=frozenset()) -> list[list[dict]]:
    """The rows of ONE unit split into attempts (oldest first): a multi-turn session by :func:`session_attempts`,
    a single-call unit by ``run_id``."""
    rows = list(rows)
    if any(_numbered(r) for r in rows):
        return session_attempts(rows, refusal_ids)
    groups: dict = {}
    for i, r in enumerate(rows):
        g = groups.setdefault(r.get("run_id") or ("anon", i), {"stamp": (str(r.get("ts") or ""), i), "rows": []})
        g["rows"].append(r)
        g["stamp"] = min(g["stamp"], (str(r.get("ts") or ""), i))
    return [g["rows"] for g in sorted(groups.values(), key=lambda g: g["stamp"])]


def first_reply_row(rows: Iterable[dict], refusal_ids=frozenset()) -> dict:
    """The scored row among several rows of ONE turn (a joined continuation, or a single-call unit): the oldest row
    that is not an infrastructure failure, ordered by (ts, file order); when every row is one, the newest (the
    turn stays missing). Pure: the rows are not written to."""
    rows = list(rows)
    order = _order(rows)
    good = [i for i in order if not is_infra(rows[i], refusal_ids)]
    return rows[good[0] if good else order[-1]]


def scored(rows: Iterable[dict], refusal_ids=frozenset()) -> tuple[list[dict], int]:
    """(the rows that score, number of attempts) for ONE unit: the first attempt with no infrastructure failure
    (else the last attempt); a session's rows reduced to one row per turn (:func:`session_rows`)."""
    rows = list(rows)
    att = attempts(rows, refusal_ids)
    if not att:
        return [], 0
    failed = [_attempt_failed(a, refusal_ids) for a in att]
    first = next((i for i, bad in enumerate(failed) if not bad), len(att) - 1)
    kept = att[first]
    if any(_numbered(r) for r in kept):
        kept = session_rows(kept, refusal_ids)
    return kept, len(att)


def pick(rows: Iterable[dict], refusal_ids=frozenset()) -> tuple[list[dict], dict | None]:
    """(the rows that score, mark) for one unit. ``mark`` = ``{"ignored": later attempts left out, "replaced_infra":
    earlier infrastructure failures a later attempt replaced, "kept": run id}`` or None when the unit had one attempt
    and no turn with more than one row."""
    rows = list(rows)
    att = attempts(rows, refusal_ids)
    kept, n = scored(rows, refusal_ids)
    if n < 2 and len(kept) == len(rows):
        return rows, None
    failed = [_attempt_failed(a, refusal_ids) for a in att]
    first = next((i for i, bad in enumerate(failed) if not bad), len(att) - 1)
    mark = {"ignored": len(att) - 1 - first, "replaced_infra": first, "kept": kept[0].get("run_id") if kept else None}
    return kept, mark


def select(rows: list[dict], refusal_ids=frozenset()) -> list[dict]:
    """``rows`` (one run's rows of many units, file order kept) with every unit reduced to its scored attempt. A unit
    that had other attempts carries ``attempts45`` on a COPY of each kept row; the input rows are never written to."""
    jobs: dict[tuple, list[int]] = {}
    for i, r in enumerate(rows):
        jobs.setdefault((r.get("case_id"), r.get("repeat")), []).append(i)
    drop: set[int] = set()
    marks: dict[int, dict] = {}
    for idx in jobs.values():
        sub = [rows[i] for i in idx]
        if not rule_on(sub[0]):
            continue
        kept, mark = pick(sub, refusal_ids)
        if mark is None:
            continue
        keep_ids = {id(r) for r in kept}
        for i in idx:
            if id(rows[i]) in keep_ids:
                marks[i] = mark
            else:
                drop.add(i)
    return [({**r, "attempts45": marks[i]} if i in marks else r) for i, r in enumerate(rows) if i not in drop]


def summary(rows: Iterable[dict]) -> dict | None:
    """``{"jobs": n, "ignored": total, "replaced_infra": total, "by_job": {"<case>#r<repeat>": mark}}`` over the board
    rows that carry an ``attempts45`` mark, or None. report.json carries it as ``attempts45`` beside ``reruns45``; the
    per-model count is ``ignored``."""
    by: dict[str, dict] = {}
    for r in rows:
        m = r.get("attempts45")
        if isinstance(m, dict):
            by.setdefault(f"{r.get('case_id')}#r{r.get('repeat') or 1}", dict(m))
    if not by:
        return None
    return {"jobs": len(by), "ignored": sum(int(m.get("ignored") or 0) for m in by.values()),
            "replaced_infra": sum(int(m.get("replaced_infra") or 0) for m in by.values()),
            "by_job": dict(sorted(by.items()))}
