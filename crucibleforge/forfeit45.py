"""Forfeits (4.5.1): an owner-declared record that one job the provider never answered counts as a failed answer.

A job (model, case, repeat) whose every try ended in a transport failure is MISSING (scoring45.job_missing_reason
"error"), and a model with a missing job is not ranked. When the provider keeps failing on the same job, the person
running the benchmark may declare that job a forfeit:

    crucibleforge --results DIR forfeit --models LABEL --case CASE [--repeat N] --reason "TEXT" [--undo]

The record lives in ``forfeits.json`` beside the run's stored rows (a list of ``{model, case_id, repeat, reason,
declared, attempts: [{ts, detail}]}``). It is never written automatically, and only for a job that is MISSING with
reason ``error`` after at least MIN_ATTEMPTS stored error rows, none of them a provider policy block (a refusal is
the model side's answer, scored by its own rule, never a forfeit).

With a record on file the report scores that job exactly as a failed answer (report._forfeit_failed, applied where
the board reads its rows, report.board_view: grade fail, 0 points, every check failed, and on a Fixes unit the bug-fix
checker's verdict for an empty reply), so it enters the totals, the ranges and the rank like any other failed job, and the
model's notes say so. The stored rows are never changed: the record is read at load time, in memory only. A record
whose job is no longer MISSING (a later run answered it) has no effect: the answer counts.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .config import results_dir

log = logging.getLogger(__name__)

FILENAME = "forfeits.json"
#: stored error rows a job needs before it can be declared a forfeit
MIN_ATTEMPTS = 3
#: the row key (and _counted_failed reason) a forfeited job's rows carry in memory
ROW_KEY = "forfeit"

_CACHE: dict = {}


def path(folder: Path | str | None = None) -> Path:
    return Path(folder or results_dir()) / FILENAME


def _norm_repeat(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def key_of(case_id, repeat) -> tuple[str, int | None]:
    return (str(case_id), _norm_repeat(repeat))


def load(folder: Path | str | None = None) -> list[dict]:
    """The records of one results folder ([] when there is no file). An unreadable file is logged and read as no
    record at all: the jobs then stay MISSING (not ranked), which is the safe side."""
    p = path(folder)
    try:
        st = p.stat()
    except OSError:
        return []
    ck = (str(p), st.st_mtime_ns, st.st_size)
    if ck in _CACHE:
        return _CACHE[ck]
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(body, list) or not all(isinstance(x, dict) for x in body):
            raise ValueError("not a list of records")
    except (OSError, ValueError) as e:
        log.warning("%s unreadable (%s): no forfeit applied", p, e)
        body = []
    _CACHE.clear()
    _CACHE[ck] = body
    return body


def records_for(label: str, folder: Path | str | None = None) -> dict[tuple, dict]:
    """{(case_id, repeat): record} of one model."""
    return {key_of(r.get("case_id"), r.get("repeat")): r for r in load(folder)
            if r.get("model") == label and r.get("case_id")}


def apply(label: str, rows: list[dict], refusal_ids, fail_row) -> list[dict]:
    """The board rows of ``label`` with each forfeited job's rows replaced by ``fail_row(row)``, the failed answer
    (report._forfeit_failed), in memory. A record applies only while its job is MISSING with reason ``error`` on these
    rows (the newest run of each job). ``rows`` is returned unchanged (same object) when nothing applies."""
    recs = records_for(label)
    if not recs:
        return rows
    from .scoring45 import job_missing_reason
    jobs: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("category") != "perf":
            jobs[key_of(r.get("case_id"), r.get("repeat"))].append(r)
    hit = {k for k in recs if k in jobs and job_missing_reason(jobs[k], refusal_ids) == "error"}
    if not hit:
        return rows
    return [fail_row(r) if r.get("category") != "perf" and key_of(r.get("case_id"), r.get("repeat")) in hit else r
            for r in rows]


def applied(label: str, by_t: dict[int, list[dict]]) -> list[dict]:
    """The forfeits in force on these rows (``report.json`` ``forfeits``): case_id, repeat, reason, attempts (count),
    declared. [] when no row is a forfeited one."""
    keys = {key_of(r.get("case_id"), r.get("repeat")) for rows in by_t.values() for r in rows if r.get(ROW_KEY)}
    if not keys:
        return []
    recs = records_for(label)
    return [{"case_id": recs[k].get("case_id"), "repeat": recs[k].get("repeat"),
             "reason": recs[k].get("reason") or "", "attempts": len(recs[k].get("attempts") or []),
             "declared": recs[k].get("declared")}
            for k in sorted(keys, key=lambda k: (k[0], k[1] or 0)) if k in recs]


def split_missing(by_t: dict[int, list[dict]], refusal_ids=frozenset()) -> tuple[dict, list[dict]]:
    """scoring45.split_missing over every job but the forfeited ones (rows marked ROW_KEY by :func:`apply`), which
    stay scored as they stand: a failed answer. Row order is kept. scoring45 itself is not changed, so the scorer
    stamp does not move for a record that is data, not code. Without a forfeited row it is split_missing itself."""
    from .scoring45 import split_missing as _split
    if not any(r.get(ROW_KEY) for rows in by_t.values() for r in rows):
        return _split(by_t, refusal_ids)
    rest, missing = _split({t: [r for r in rows if not r.get(ROW_KEY)] for t, rows in by_t.items()}, refusal_ids)
    kept = {id(r) for rows in rest.values() for r in rows}
    return {t: [r for r in rows if r.get(ROW_KEY) or id(r) in kept] for t, rows in by_t.items()}, missing


def note(forfeits: list[dict]) -> str | None:
    """The model's one plain line: "1 job counted as 0: PATCHFIX-core-4-1, the provider never answered in 5 tries"."""
    if not forfeits:
        return None
    n = len(forfeits)

    def one(f: dict) -> str:
        rep = _norm_repeat(f.get("repeat"))
        return f"{f.get('case_id')}" + (f" repeat {rep}" if rep not in (None, 1) else "")

    if n == 1:
        f = forfeits[0]
        return (f"1 job counted as 0: {one(f)}, the provider never answered in "
                f"{f.get('attempts') or 'several'} tries")
    return (f"{n} jobs counted as 0, the provider never answered: "
            + ", ".join(f"{one(f)} ({f.get('attempts') or 'several'} tries)" for f in forfeits))


# ------------------------------------------------------------------ the CLI command

def _error_attempts(folder: Path, label: str, case_id: str, repeat: int) -> list[dict]:
    """The stored ``grade == error`` rows of one job in ``runs.csv`` (numeric and status fields only)."""
    p = Path(folder) / "runs.csv"
    if not p.is_file():
        return []
    csv.field_size_limit(1 << 30)
    out = []
    with open(p, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("model_label") == label and r.get("case_id") == case_id
                    and _norm_repeat(r.get("repeat")) == repeat and r.get("grade") == "error"):
                out.append(r)
    return out


def _write(folder: Path, records: list[dict]) -> None:
    p = path(folder)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(records, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def missing_reason(label: str, cfg: dict | None, case_id: str, repeat: int) -> str | None:
    """Why the job is MISSING on this folder's board right now (None = it is scored), from the report's own rows."""
    from . import report
    rows, era = report.board_view(label, cfg)
    st = report.tier_model_stats(label, cfg, rows, report.load_meta(label), era)
    for m in st.get("missing_jobs") or []:
        if m.get("case_id") == case_id and _norm_repeat(m.get("repeat")) == repeat:
            return m.get("reason")
    return None


def declare(folder: Path, label: str, case_id: str, repeat: int, reason: str, cfg: dict | None,
            undo: bool = False, now: str | None = None) -> tuple[int, str]:
    """(exit code, one line). Writes or removes the record; refuses (exit 1) when the job does not qualify."""
    folder = Path(folder)
    recs = load(folder)
    k = key_of(case_id, repeat)
    mine = [r for r in recs if r.get("model") == label and key_of(r.get("case_id"), r.get("repeat")) == k]
    if undo:
        if not mine:
            return 1, f"forfeit: no record for {label} {case_id} repeat {repeat} in {path(folder)}"
        _write(folder, [r for r in recs if r not in mine])
        return 0, f"forfeit removed: {label} {case_id} repeat {repeat} (the job is MISSING again)"
    if mine:
        return 1, f"forfeit: {label} {case_id} repeat {repeat} is already on record (use --undo first)"
    if not (reason or "").strip():
        return 1, "forfeit: --reason is required (say why, in plain words)"
    errs = _error_attempts(folder, label, case_id, repeat)
    if len(errs) < MIN_ATTEMPTS:
        return 1, (f"forfeit refused: {label} {case_id} repeat {repeat} has {len(errs)} stored error row(s) in "
                   f"runs.csv; at least {MIN_ATTEMPTS} are needed")
    from .scoring45 import _policy_block
    blocked = [r for r in errs if _policy_block({**r, "error": r.get("grade_detail")})]
    if blocked:
        return 1, (f"forfeit refused: {len(blocked)} of the job's error rows are a provider policy block "
                   "(a refusal is scored by its own rule, never a forfeit)")
    why = missing_reason(label, cfg, case_id, repeat)
    if why != "error":
        return 1, (f"forfeit refused: {label} {case_id} repeat {repeat} is "
                   + (f"MISSING with reason {why!r}, not 'error'" if why else "not MISSING (it is scored)"))
    rec = {"model": label, "case_id": case_id, "repeat": repeat, "reason": reason.strip(),
           "declared": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "attempts": [{"ts": r.get("ts"), "detail": r.get("grade_detail")} for r in errs]}
    _write(folder, recs + [rec])
    return 0, (f"forfeit recorded: {label} {case_id} repeat {repeat} counts as a failed answer "
               f"({len(errs)} tries on record in {path(folder)})")
