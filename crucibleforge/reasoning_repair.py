"""Re-judge rows whose answer was left in the reasoning channel — no generation.

Before the multi-turn and judged paths shared the single-turn policy
(:func:`api.reasoning_answer`), a reply that FINISHED (finish=stop) with an
empty content channel, no tool call and its whole answer in the reasoning
channel was recorded as an empty turn: the session's stored ``conversation``
carried ``""`` for it and the judge scored the row an empty generation (0).
Nanbeige4.3-33B did it on 27 of 122 rows, all multi-turn.

This module finds such rows in an existing transcript and, on ``apply``,
appends a repaired copy of each judged row (the loader's last-row-wins dedupe
makes it the effective row; nothing is overwritten or deleted):

* a session's final (judged) row gets its ``conversation`` rebuilt with each
  affected turn's reasoning-channel answer (from that turn's own row), its
  checks / prose metrics recomputed from the repaired replies, and
  ``answered_in_reasoning_turns`` listing the repaired turns;
* a single-turn judged row is stamped ``answered_in_reasoning`` (the judge
  reads :func:`api.row_answer` already);
* the stored verdict is moved to ``judge_before_reasoning_repair`` so the row
  is PENDING again; ``crucibleforge judge --models <label> --cases <ids>``
  then judges exactly these rows from the stored text.

Truncated rows (finish=length) are never touched — an overflow is not an
answer. Caveat: the model GENERATED its later turns without seeing its own
earlier reasoning-channel replies (the old runner dropped an empty assistant
turn and joined the user turns around it); a re-judge scores what it wrote,
only a regeneration replays the session as the fixed runner now sends it.
"""
from __future__ import annotations

from collections import defaultdict

from .api import row_answer
from .config import append_transcript, load_transcripts, results_dir

REPAIR_TAG = "reasoning_answer_repair"


def _affected_turn(row: dict) -> bool:
    """A row whose content is empty but whose reasoning holds a finished answer."""
    return not (row.get("response") or "").strip() and bool(row_answer(row).strip())


def _session_key(row: dict) -> tuple:
    return (row.get("bench_run_id"), row.get("case_id"), row.get("repeat"))


def _repair_session(final: dict, turns: dict[int, dict], cases_by_id: dict) -> dict | None:
    """The final row with its conversation's assistant turns filled from the
    reasoning-channel answers, or None when nothing changes."""
    conv = final.get("conversation") or []
    new_conv, fixed, k = [], [], 0
    for m in conv:
        if m.get("role") != "assistant":
            new_conv.append(m)
            continue
        k += 1
        t = turns.get(k)
        if t is not None and not (m.get("content") or "").strip() and _affected_turn(t):
            new_conv.append({**m, "content": row_answer(t)})
            fixed.append(k)
        else:
            new_conv.append(m)
    if not fixed:
        return None
    row = {**final, "conversation": new_conv, "answered_in_reasoning_turns": fixed}
    if _affected_turn(final):
        row["answered_in_reasoning"] = True
    replies = [m.get("content") or "" for m in new_conv if m.get("role") == "assistant"]
    case = cases_by_id.get(final.get("case_id"))
    if case is not None:
        from . import session_checks
        chk = session_checks.run_checks(case, replies)
        if chk:
            row["checks"] = chk
    if "prose" in final or (case or {}).get("category") in _creative():
        written = "\n\n".join(r for r in replies if r.strip())
        if written:
            from .graders import prose_metrics
            row["prose"] = prose_metrics(written)
    return row


def _creative() -> set:
    try:
        from .runner import CREATIVE_CATEGORIES
        return set(CREATIVE_CATEGORIES)
    except Exception:  # noqa: BLE001
        return set()


def _pend(row: dict) -> dict:
    """Move a stored verdict aside so the row is pending for the judge."""
    out = dict(row)
    if "judge" in out:
        out["judge_before_reasoning_repair"] = out.pop("judge")
        out.pop("judge_model", None)
    out[REPAIR_TAG] = True
    return out


def plan(label: str, cases_by_id: dict | None = None,
         skipped: list | None = None) -> list[dict]:
    """The repairs for one transcript label: ``[{"row": repaired_row,
    "case_id", "turn", "kind", "turns", "verdict"}]``. Read-only. Sessions
    whose repair cannot change the verdict go to ``skipped``."""
    skipped = skipped if skipped is not None else []
    rows = load_transcripts(label)
    cases_by_id = cases_by_id or {}
    sessions: dict[tuple, dict[int, dict]] = defaultdict(dict)
    for r in rows:
        if r.get("turn") is not None:
            sessions[_session_key(r)][int(r["turn"])] = r
    out = []
    for r in rows:
        if not r.get("needs_judge") or r.get(REPAIR_TAG):
            continue
        if r.get("conversation"):
            fixed = _repair_session(r, sessions.get(_session_key(r), {}), cases_by_id)
            if fixed is None:
                continue
            kind, turns = "session", fixed["answered_in_reasoning_turns"]
        elif r.get("turn") is None and _affected_turn(r):
            fixed = {**r, "answered_in_reasoning": True}
            kind, turns = "single", []
        else:
            continue
        if kind == "session" and r.get("rubric") != "rp_deep":
            last = [m for m in fixed["conversation"] if m.get("role") == "assistant"][-1:]
            if not last or not (last[0].get("content") or "").strip():
                # the judged final turn is still empty (an overflow): the
                # judge scores the session an empty generation either way
                # (judge._has_content) — making it pending changes nothing
                skipped.append({"case_id": r.get("case_id"), "repeat": r.get("repeat"),
                                "bench_run_id": r.get("bench_run_id"), "turns": turns,
                                "why": "final turn still empty (finish=length)"})
                continue
        j = r.get("judge")
        verdict = ("pending" if j is None else
                   "empty_generation" if isinstance(j, dict) and j.get("empty_generation")
                   else "judged")
        out.append({"row": _pend(fixed), "case_id": r.get("case_id"), "turn": r.get("turn"),
                    "repeat": r.get("repeat"), "bench_run_id": r.get("bench_run_id"),
                    "kind": kind, "turns": turns, "verdict": verdict})
    return out


def labels_all() -> list[str]:
    return sorted(p.name[len("transcripts_"):-len(".jsonl")]
                  for p in results_dir().glob("transcripts_*.jsonl"))


def run(labels: list[str], apply: bool = False, cases_by_id: dict | None = None) -> dict:
    """Plan (and with ``apply``, append) the repairs. Returns
    ``{label: {"rows": [plan items without the row], "skipped": [...]}}``."""
    report = {}
    for label in labels:
        skipped: list = []
        items = plan(label, cases_by_id, skipped)
        if apply:
            for it in items:
                append_transcript(label, it["row"])
        report[label] = {"rows": [{k: v for k, v in it.items() if k != "row"} for it in items],
                         "skipped": skipped}
    return report
