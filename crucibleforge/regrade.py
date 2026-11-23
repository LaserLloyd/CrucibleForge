"""Regrade stored rows in place after a grading-only change: no regeneration.

    python -m crucibleforge.regrade MODEL --reason TEXT [--profile NAME] [--part P]
                                    [--switch NAME] [--go] [--json]

A grading-only change (a check's weight, an accepted answer form, a pin bump that changes no
prompt) moves a family's revision stamp, so the stored rows read as stale and a resume would
re-make them. This tool rebuilds every stale stored attempt's instance from the current tree
(profile, release seed, family code) and compares what the model was SENT, byte for byte:

* the instance block (family, level, variant, seed, ceiling, pass mark, ran-out rule, ...);
  any difference refuses the attempt (a new ceiling or seed is a new instance);
* the system text and the prompt (single and multi-item rows); for a session every scripted
  user turn, the system text of the stored conversation and the card history's length;
* judged elements: a stored judge verdict is carried over unchanged, so the elements the judge
  reads must be identical; otherwise the attempt goes to a re-judge run, never a restamp.

An identical attempt is graded again by the code the live run uses
(``runner.grade_single_row``, ``session_checks.run_checks``, ``chains.stamp_verdict``) and
written as SUPERSEDING rows: a copy of each of its rows with the new grade, the new
``chain_revision`` and ``regraded_by: {tool, commit, reason, at, from_revision, switch,
regraded, carried}``. ``config.load_transcripts`` keeps the last row per (bench_run_id,
case_id, repeat, turn), so the copies replace the old rows for every reader (resume,
elemstats, scale, the board) and nothing else on the row changes: model, sampler, protocol,
ceiling sent, cost and metrics are the run's own. A second run finds the rows current and
writes nothing (idempotent). Dry run by default; ``--go`` appends.

Not regraded (refused with a reason): a session made under an older run rule that changed what
later turns were sent (:func:`session_rule_refusal`: a degenerate turn fed back as the reply,
before f70f9ee; a regenerate that replayed the first attempt, before 4a311c2); console units (their user turns come from the
simulator; second cut), static case rows outside chains and packs (second cut), routed rows keep their
route untouched (the route is copied, never recomputed).

Pack questions (a case written inside a ladder set file; no chain block) are regraded in the same pass
(:func:`plan_pack`), wherever they sit and under any composition: the question is rebuilt from the
current tree and must have been sent byte-identically; it then carries the current case hash.

Grading switches (``--switch``): the instance's ``ran_out`` rule may differ from the stored
one only with ``--switch ran_out``; the profile's empty-stop / degenerate / speaker-tag-lead
rules are the ones in force now and are named in ``regraded_by.switch`` when they are the
reason.
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

from . import chains, config, profiles

#: chain-block keys that are the verdict, not the instance
VERDICT_KEYS = frozenset(("score", "passed", "outcome", "elements", "cut_off", "context_exhausted",
                          "judged"))
#: row keys a single-row grade writes (cleared before grading again)
ROW_GRADE_KEYS = ("grade", "grade_detail", "elements", "score", "format_errors", "applied", "env",
                  "tamper", "needs_judge", "rubric", "reference", "judge_key", "checks", "prose")
#: the grading switches a regrade may name as its reason
SWITCHES = ("ran_out", "empty_stop", "degenerate", "speaker_tag_lead", "element_weight", "answer_sheet")
#: switches whose change is in the shared chain grading (chains.py), not in a family's source: the
#: chain revision does not move, so the tool regrades CURRENT attempts too and supersedes only
#: those whose verdict changes (element_weight: a weight-0 check stays weight 0, 2026-11-14;
#: answer_sheet: a full literal "N." knowledge sheet reads in order, graders.literal_n_numbered,
#: 2026-11-15 -- a graders.py change, so no family revision moves)
RECHECK_SWITCHES = ("element_weight", "answer_sheet")
#: instance-block keys that may differ only when the regrade's switch is that key
SWITCH_BLOCK_KEYS = {"ran_out": "ran_out"}


def _commit() -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10, check=False)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _first_diff(a, b) -> str:
    """Where two stored texts first differ: ``char N: 'old…' vs 'new…'``."""
    a, b = "" if a is None else str(a), "" if b is None else str(b)
    n =next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return f"char {n}: {a[n:n + 40]!r} vs {b[n:n + 40]!r} (lengths {len(a)} / {len(b)})"


def attempts(rows: list[dict]) -> list[list[dict]]:
    """The stored chain attempts: rows grouped by (bench_run_id, unit, rung, attempt, repeat),
    file order kept, each group sorted by turn (a single row has turn None)."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        ch = r.get("chain")
        if not ch:
            continue
        k = (r.get("bench_run_id"), ch.get("id"), ch.get("rung"), ch.get("attempt"), r.get("repeat"))
        groups.setdefault(k, []).append(r)
    return [sorted(g, key=lambda r: r.get("turn") or 0) for g in groups.values()]


def superseded_runs(rows: list[dict]) -> dict[tuple, str]:
    """{(unit, rung, attempt, repeat): bench_run_id of the attempt the board reads}: the newest graded row of
    each instance wins, by (ts, file order) -- the rule of chains.results_from_rows. An attempt group of
    another run is superseded and is never regraded or restamped: a regrade sets ts to now, so restamping
    an older attempt beside a newer one that stays untouched (current) would make the older one the newest
    and the board would read it (lead 2026-11-15)."""
    newest: dict[tuple, tuple] = {}
    for i, r in enumerate(rows):
        ch = r.get("chain")
        if not ch or ch.get("passed") is None:
            continue
        k = (ch.get("id"), ch.get("rung"), ch.get("attempt"), r.get("repeat"))
        stamp = (str(r.get("ts") or ""), i)
        if k not in newest or stamp >= newest[k][0]:
            newest[k] = (stamp, r.get("bench_run_id"))
    return {k: run for k, (_s, run) in newest.items()}


def _superseded_by(grp: list[dict], winners: dict[tuple, str]) -> str | None:
    """The run whose newer attempt supersedes this attempt group, else None."""
    last = grp[-1]
    ch = last["chain"]
    win = winners.get((ch.get("id"), ch.get("rung"), ch.get("attempt"), last.get("repeat")))
    return win if win is not None and win != last.get("bench_run_id") else None


def _current(rows: list[dict]) -> bool:
    return all(r.get("chain_revision") == chains.revision(r["chain"]) for r in rows)


def _turn(r: dict) -> int:
    t = r.get("turn")
    return t if isinstance(t, int) else 0


def replayed_regenerate(row: dict) -> bool:
    """The row's empty-stop regenerate replayed the first attempt (Admin X, 2026-11-14): no
    ``retry_seed`` on ``empty_stop_retry`` (made before regen-fresh-seed 4a311c2, which resent
    the case seed, so a seeded server gave the same empty reply again) AND the first attempt's
    completion tokens and reasoning tail equal the final reply's. A row without ``retry_seed``
    whose retry differed is a real regenerate and stays valid."""
    es = row.get("empty_stop_retry")
    if not isinstance(es, dict) or es.get("retry_seed") is not None:
        return False
    if es.get("mode", "regenerate") != "regenerate":
        return False
    m = row.get("metrics") or {}
    return (es.get("first_completion_tokens") == m.get("completion_tokens")
            and es.get("first_reasoning_tail") == (row.get("reasoning") or "")[-60:])


def session_rule_refusal(rows: list[dict]) -> str | None:
    """Why a stored attempt (a session: all its turns) was made under an older run rule that
    changed what later turns were SENT, so no regrade can stand for it (re-run it), else None:

    * a turn before the last that the degenerate test flags went into history as the loop
      (made before f70f9ee: now it goes into history EMPTY), so every later turn was sent a
      history the current run would not send;
    * a turn whose regenerate replayed the first attempt (:func:`replayed_regenerate`): the
      turn and every later one rest on a retry that was not one.
    """
    from . import degenerate as _dg
    turns = sorted(rows, key=_turn)
    for r in turns[:-1]:
        if (r.get("degenerate") or {}).get("history") == "empty" or (r.get("empty_stop") or {}).get("history") == "empty":
            continue
        try:
            hit = _dg.row_degenerate(r)
        except Exception:  # noqa: BLE001 - a test that cannot read the row refuses nothing
            hit = None
        if hit:
            return (f"turn {_turn(r)} is degenerate ({hit.get('channel')}) and went into history as the "
                    f"reply (a session made before f70f9ee): later turns were sent a history the "
                    f"current rule sends empty; re-run it")
    for r in turns:
        if replayed_regenerate(r):
            return (f"turn {_turn(r)}: its regenerate replayed the first attempt (no retry_seed: made "
                    f"before regen-fresh-seed 4a311c2) and later turns were sent a history a real "
                    f"regenerate would have changed; re-run it")
    return None


#: the judge_mode keys the JUDGE CALL reads (judge.judge_policy: rubric mode, thinking off, the output cap). Every
#: other key (axes, sexual_axes, dead_axes, combine, code_weight) is read only when the board scores the stored
#: verdict, and the board reads the PROFILE's judge_mode (chains.section_stats -> rubric_element), so a verdict stays
#: valid when only those change
JUDGE_MODE_CALL_KEYS = frozenset(("kind", "max_tokens"))


def _report_time_only(key: str, new, old) -> bool:
    """True when an instance-block difference is only in report-time judge_mode keys (CHAT-RP 2026-11-14: rc3's
    axes list and the weakest-live-mark change would otherwise refuse every stored role-play attempt, i.e. a
    re-run of work whose stored verdict is still the right one)."""
    if key != "judge_mode" or not isinstance(new, dict) or not isinstance(old, dict):
        return False
    # a ladder-mode block asks the same one-call rubric verdict (judge.row_judge_mode): rubric <-> ladder is
    # judge- and report-time only (the ladder pass reads the stored replies; nothing is re-run)
    strip = lambda d: {k: ("rubric" if (k == "kind" and v == "ladder") else v) for k, v in d.items()
                       if k in JUDGE_MODE_CALL_KEYS}
    return strip(new) == strip(old)


def _compare_sent(rows: list[dict], case: dict) -> str | None:
    """None when what was sent equals what the current tree sends, else the first difference."""
    last = rows[-1]
    if case.get("turns"):
        turns = case["turns"]
        for r in rows:
            t = r.get("turn")
            if not isinstance(t, int) or not 1 <= t <= len(turns):
                return f"turn {t!r} is outside the instance's {len(turns)} turns"
            if r.get("prompt") is not None and r["prompt"] != turns[t - 1]:
                return f"turn {t} user text differs at {_first_diff(r['prompt'], turns[t - 1])}"
        conv = last.get("conversation")
        if conv is None:
            return "the final row carries no conversation (cannot compare the system text)"
        sys_ = [m.get("content") for m in conv if m.get("role") == "system"]
        # an empty system prompt is sent as no system message: "" and None are the same (as in the single-row
        # path below); jcard's person cards have system "" and were refused on this (CHAT-RP 2026-11-14)
        if ((sys_[0] if sys_ else None) or None) != (case.get("system") or None):
            return f"system text differs at {_first_diff(sys_[0] if sys_ else None, case.get('system'))}"
        users = [m.get("content") for m in conv if m.get("role") == "user"]
        if users != turns[:len(users)]:
            k = next(i for i, (u, t) in enumerate(zip(users, turns)) if u != t) if any(
                u != t for u, t in zip(users, turns)) else len(turns)
            return f"conversation user turn {k + 1} differs" + (
                f" at {_first_diff(users[k], turns[k])}" if k < min(len(users), len(turns)) else "")
        if int(last.get("history_len") or 0) != len(case.get("history") or []):
            return (f"card history length differs ({last.get('history_len') or 0} stored, "
                    f"{len(case.get('history') or [])} now)")
        return None
    if last.get("prompt") != case.get("prompt"):
        return f"prompt differs at {_first_diff(last.get('prompt'), case.get('prompt'))}"
    if (last.get("system") or None) != (case.get("system") or None):
        return f"system text differs at {_first_diff(last.get('system'), case.get('system'))}"
    return None


def _result_of(row: dict):
    from .api import ChatResult
    m = row.get("metrics") or {}
    return ChatResult(response_text=row.get("response") or "", reasoning_text=row.get("reasoning") or "",
                      tool_calls=row.get("tool_calls") or [], finish_reason=row.get("finish_reason"),
                      served_model=row.get("served_model"), reasoning_tokens=m.get("reasoning_tokens"),
                      completion_tokens=m.get("completion_tokens"), prompt_tokens=m.get("prompt_tokens"))


def _regrade_rows(rows: list[dict], case: dict, rules: dict) -> list[dict]:
    """Copies of one attempt's rows graded by the live run's code under ``rules``."""
    from . import degenerate, runner, session_checks
    out = copy.deepcopy(rows)
    blk = copy.deepcopy(case["chain"])
    for r in out:
        start = (r.get("chain") or {}).get("start_rung")
        r["chain"] = copy.deepcopy(blk)
        if start is not None:
            r["chain"]["start_rung"] = start
    last = out[-1]
    if case.get("turns"):
        conv = last.get("conversation") or []
        replies = [m.get("content") or "" for m in conv if m.get("role") == "assistant"]
        if any(chains._is_ctx(r) for r in out):
            n = len(case["turns"])
            replies = (replies + [""] * n)[:n]
        last.pop("checks", None)
        chk = session_checks.run_checks(case, replies)
        if chk:
            last["checks"] = chk
    elif not case.get("multi_items"):
        for r in out:
            if r.get("skipped") or r.get("error") or r.get("grade") == "error":
                continue
            judge = r.get("judge")
            for k in ROW_GRADE_KEYS:
                r.pop(k, None)
            res = _result_of(r)
            blank = bool(rules.get("empty_stop_rule")) and bool(r.get("empty_stop") or degenerate.is_degenerate(r))
            runner.grade_single_row(r, res, case, blank, r.get("max_tokens_sent"))
            if judge is not None:
                r["judge"] = judge
    chains.stamp_verdict(out, case)
    return out


def plan(rows: list[dict], specs: dict[str, dict], reason: str, switch: str | None = None,
         rules: dict | None = None, part: str | None = None, now: str | None = None,
         commit: str | None = None, pin: str | None = None) -> list[dict]:
    """One record per stored attempt: ``status`` current / regrade / refused, with
    ``rows`` (the superseding rows) on a regrade. ``pin``: the profile's grading interpreter
    (gen/_det.py); every attempt that would be regraded is refused when this process grades on
    another one (a work clock or timing grade moves between Python builds)."""
    rules = rules or {}
    from .gen import _det
    wrong_py = _det.pin_mismatch({"grader_pins": {"python": pin}} if pin else None)
    if switch is not None and switch not in SWITCHES:
        raise ValueError(f"unknown switch {switch!r} (known: {', '.join(SWITCHES)})")
    at = now or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    recs = []
    winners = superseded_runs(rows)
    for grp in attempts(rows):
        last = grp[-1]
        old = last["chain"]
        if part and old.get("part") != part:
            continue
        rec = {"unit": old.get("id"), "rung": old.get("rung"), "attempt": old.get("attempt"),
               "bench_run_id": last.get("bench_run_id"), "repeat": last.get("repeat"),
               "part": old.get("part"), "set": old.get("ladder"), "family": old.get("family"),
               "from_revision": last.get("chain_revision"), "score_before": old.get("score")}
        recs.append(rec)
        newer = _superseded_by(grp, winners)
        if newer:
            rec.update(status="superseded", why=f"a newer attempt (run {newer}) is the one the board reads")
            continue
        if old.get("passed") is None:
            infra = any(r.get("error") or r.get("skipped") or r.get("grade") == "error" for r in grp)
            rec.update(status="refused", why="an errored attempt (infrastructure): it is re-run, never regraded"
                       if infra else "the attempt has no stored verdict (incomplete)")
            continue
        recheck = switch in RECHECK_SWITCHES and _current(grp)
        if _current(grp) and not recheck:
            rec["status"] = "current"
            continue
        spec = specs.get(old.get("id"))
        if spec is None:
            rec.update(status="refused", why="the unit is not in this profile")
            continue
        why = session_rule_refusal(grp) or effort_refusal(grp)
        if why:
            rec.update(status="refused", why=why)
            continue
        try:
            inst = chains.instance(spec, int(old["rung"]), int(old["attempt"]),
                                   start=int(old.get("start_rung") or 1))
        except (IndexError, KeyError, TypeError, ValueError) as e:
            rec.update(status="refused", why=f"the profile has no such rung/attempt ({e})")
            continue
        new = inst["chain"]
        allow = {SWITCH_BLOCK_KEYS[switch]} if switch in SWITCH_BLOCK_KEYS else set()
        diff = sorted(k for k in (set(new) | set(old)) - VERDICT_KEYS - {"start_rung"} - allow
                      if new.get(k) != old.get(k) and not _report_time_only(k, new.get(k), old.get(k)))
        if diff:
            rec.update(status="refused", why="the instance differs: " + ", ".join(
                f"{k} {old.get(k)!r} -> {new.get(k)!r}" for k in diff))
            continue
        try:
            case = chains.materialize(inst)
        except Exception as e:  # noqa: BLE001 - a family that cannot build the instance refuses it
            rec.update(status="refused", why=f"the instance could not be built: {e}"[:200])
            continue
        if case.get("console"):
            rec.update(status="refused", why="console unit: not regraded by this tool yet")
            continue
        why = _compare_sent(grp, case)
        if why:
            rec.update(status="refused", why=f"what was sent differs: {why}")
            continue
        stored_je = next((r.get("judge_elements") for r in reversed(grp) if r.get("judge_elements")), None)
        if (stored_je or case.get("judge_elements")) and stored_je != case.get("judge_elements"):
            rec.update(status="refused", why="judged elements changed: needs a re-judge run, not a restamp")
            continue
        if old.get("judged") and not stored_je and case.get("judge_elements"):
            rec.update(status="refused", why="judged unit without stored judge elements: re-judge")
            continue
        if wrong_py:
            rec.update(status="refused", why=wrong_py)
            continue
        new_rows = _regrade_rows(grp, case, rules)
        nb = new_rows[-1]["chain"]
        if recheck and all(nb.get(k) == old.get(k) for k in VERDICT_KEYS):
            rec["status"] = "current"          # the shared grading gives the stored verdict: nothing to write
            continue
        code_ids = [e.get("id") for e in nb.get("elements") or []]
        judge_ids = [e.get("id") for e in ((new_rows[-1].get("judge") or {}).get("elements") or {}).get("results") or []]
        for r_old, r in zip(grp, new_rows):
            r["chain_revision"] = chains.revision(r["chain"])
            r["ts"] = at
            r["regraded_by"] = {"tool": "crucibleforge.regrade", "commit": commit, "reason": reason,
                                "at": at, "from_revision": r_old.get("chain_revision"),
                                **({"switch": switch} if switch else {}),
                                "regraded": code_ids, "carried": judge_ids,
                                **({"previous": r_old["regraded_by"]} if r_old.get("regraded_by") else {})}
        rec.update(status="regrade", score_after=nb.get("score"), passed_before=old.get("passed"),
                   passed_after=nb.get("passed"), to_revision=new_rows[-1]["chain_revision"], rows=new_rows)
    return recs


#: case keys --stamp may record on stored rows: facts of the case that change no grade
STAMPS = ("element_standard", "check_tier", "pack_location")
#: the row keys that carry a pack question's content stamp (version.row_case_stamp)
_PACK_STAMP_KEYS = ("case_revision", "ladder_revision")


def effort_refusal(grp: list[dict]) -> str | None:
    """A Claude Code attempt whose rows ran at different efforts is refused (lead 2026-11-14): the
    effort is a run setting, and a row without it reads as ``default``, never as an explicit one."""
    from .claudecode import row_effort
    effs = sorted({row_effort(r) for r in grp})
    return f"the attempt's rows ran at different efforts ({', '.join(effs)})" if len(effs) > 1 else None


def plan_stamp(rows: list[dict], specs: dict[str, dict], key: str, reason: str, part: str | None = None,
               now: str | None = None, commit: str | None = None) -> list[dict]:
    """``--stamp KEY``: superseding copies of each stored attempt with the case's ``KEY`` recorded on
    every row (e.g. ``element_standard: 45`` on rows made before the runner stamped it). Nothing is
    regraded and ``chain_revision`` is kept. The case must be SHOWN to be the one the row was made
    from: the unit is in the profile, the rebuilt instance block equals the stored one (family,
    level, seed, items...), and what was sent is byte-identical; otherwise the attempt is refused.
    An attempt whose case does not declare ``KEY`` is ``n/a``; one whose rows all carry it, current."""
    if key not in STAMPS:
        raise ValueError(f"unknown stamp {key!r} (known: {', '.join(STAMPS)})")
    at = now or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    recs = []
    winners = superseded_runs(rows)
    for grp in attempts(rows):
        last = grp[-1]
        old = last["chain"]
        if part and old.get("part") != part:
            continue
        rec = {"unit": old.get("id"), "rung": old.get("rung"), "attempt": old.get("attempt"),
               "bench_run_id": last.get("bench_run_id"), "repeat": last.get("repeat"),
               "part": old.get("part"), "set": old.get("ladder"), "family": old.get("family"),
               "from_revision": last.get("chain_revision"), "score_before": old.get("score"),
               "score_after": old.get("score")}
        recs.append(rec)
        newer = _superseded_by(grp, winners)
        if newer:
            rec.update(status="superseded", why=f"a newer attempt (run {newer}) is the one the board reads")
            continue
        spec = specs.get(old.get("id"))
        if spec is None:
            rec.update(status="refused", why="the unit is not in this profile: its case cannot be shown")
            continue
        why = effort_refusal(grp)
        if why:
            rec.update(status="refused", why=why)
            continue
        if not old.get("family"):
            rec.update(status="refused", why="no generator family on the row: its case cannot be shown")
            continue
        try:
            inst = chains.instance(spec, int(old["rung"]), int(old["attempt"]),
                                   start=int(old.get("start_rung") or 1))
        except (IndexError, KeyError, TypeError, ValueError) as e:
            rec.update(status="refused", why=f"the profile has no such rung/attempt ({e})")
            continue
        new = inst["chain"]
        diff = sorted(k for k in (set(new) | set(old)) - VERDICT_KEYS - {"start_rung"}
                      if new.get(k) != old.get(k))
        if diff:
            rec.update(status="refused", why="the instance differs: " + ", ".join(
                f"{k} {old.get(k)!r} -> {new.get(k)!r}" for k in diff))
            continue
        try:
            case = chains.materialize(inst)
        except Exception as e:  # noqa: BLE001
            rec.update(status="refused", why=f"the instance could not be built: {e}"[:200])
            continue
        why = _compare_sent(grp, case)
        if why:
            rec.update(status="refused", why=f"what was sent differs: {why}")
            continue
        if key == "check_tier":
            _stamp_tiers(rec, grp, case, reason, at, commit, chains.revision(new))
            continue
        if key not in case:
            rec["status"] = "n/a"
            continue
        val = case[key]
        if all(r.get(key) == val for r in grp):
            rec["status"] = "current"
            continue
        if any(key in r and r[key] != val for r in grp):
            rec.update(status="refused", why=f"the rows say {key} {next(r[key] for r in grp if key in r)!r}, "
                                             f"the case {val!r}")
            continue
        rev_now = chains.revision(new)
        new_rows = copy.deepcopy(grp)
        for r_old, r in zip(grp, new_rows):
            r[key] = copy.deepcopy(val)
            r["ts"] = at
            r["regraded_by"] = {"tool": "crucibleforge.regrade", "commit": commit, "reason": reason, "at": at,
                                "stamp": {key: val}, "from_revision": r_old.get("chain_revision"),
                                "revision_current": r_old.get("chain_revision") == rev_now,
                                "regraded": [], "carried": "all",
                                **({"previous": r_old["regraded_by"]} if r_old.get("regraded_by") else {})}
        rec.update(status="regrade", stamp={key: val}, to_revision=last.get("chain_revision"), rows=new_rows)
    return recs


def plan_pack_location(rows: list[dict], from_tree: Path, reason: str, now: str | None = None,
                       commit: str | None = None) -> list[dict]:
    """``--stamp pack_location --from-tree DIR`` (D, rc2 board 2026-11-14): pack-question rows made
    while gen.content_digest hashed the set file's ABSOLUTE path read stale under any other checkout.
    Each attempt is restamped to the current case hash only when its stored stamp is PROVEN to be
    this very case at ``DIR``: the old recipe (version.location_bound_hash) over the current case
    with ``ladder_pack`` moved under ``DIR`` must give exactly the stored stamp, so the content,
    release seed and generator output are byte-identical and only the location differed. Nothing
    is regraded; score and verdict are carried."""
    from . import ladder, version
    from .config import ROOT_DIR
    at = now or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    cases = {c["id"]: c for c in ladder.inline_cases()}
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get("chain") or r.get("case_id") not in cases:
            continue
        groups.setdefault((r.get("bench_run_id"), r.get("case_id"), r.get("repeat")), []).append(r)
    recs = []
    for (rid, cid, rep_), grp in groups.items():
        case = cases[cid]
        stamps = {version.row_case_stamp(r) for r in grp}
        rec = {"unit": cid, "rung": None, "attempt": None, "bench_run_id": rid, "repeat": rep_,
               "part": "/".join(Path(case["ladder_pack"]).parts[-3:-1]),
               "set": Path(case["ladder_pack"]).stem, "family": (case.get("generator") or {}).get("type"),
               "from_revision": next(iter(stamps)) if len(stamps) == 1 else sorted(map(str, stamps)),
               "score_before": grp[-1].get("score"), "score_after": grp[-1].get("score")}
        recs.append(rec)
        cur = version.case_hash(cid)
        if len(stamps) != 1:
            rec.update(status="refused", why=f"the attempt's rows carry different stamps {sorted(map(str, stamps))}")
            continue
        old = next(iter(stamps))
        if old == cur:
            rec["status"] = "current"
            continue
        here = Path(case["ladder_pack"]).resolve()
        try:
            rel = here.relative_to(Path(ROOT_DIR).resolve())
        except ValueError:
            rec.update(status="refused", why=f"the set file {here} is not under this checkout")
            continue
        was = version.location_bound_hash({**case, "ladder_pack": str(Path(from_tree) / rel)})
        if was != old:
            rec.update(status="refused", why=f"the stored stamp {old} is not this case under {from_tree} "
                                             f"({was}): its content differs, re-make it")
            continue
        new_rows = copy.deepcopy(grp)
        for r_old, r in zip(grp, new_rows):
            for k in _PACK_STAMP_KEYS:
                if k in r_old:
                    r[k] = cur
            r["ts"] = at
            r["regraded_by"] = {"tool": "crucibleforge.regrade", "commit": commit, "reason": reason, "at": at,
                                "stamp": {"pack_location": str(from_tree)}, "from_revision": old,
                                "to_revision": cur, "regraded": [], "carried": "all",
                                **({"previous": r_old["regraded_by"]} if r_old.get("regraded_by") else {})}
        rec.update(status="regrade", stamp={"pack_location": str(from_tree)}, to_revision=cur, rows=new_rows)
    return recs


def _pack_cases() -> dict[str, dict]:
    """Every pack question written inside a set file (ladder.inline_cases), by id. Not filtered by the
    profile's composition: a pack row is regraded wherever it sits (a board folder or a pack folder,
    under any composition), because what decides is the case the row was made from, shown below."""
    from . import ladder
    return {c["id"]: c for c in ladder.inline_cases()}


def plan_pack(rows: list[dict], reason: str, part: str | None = None, rules: dict | None = None,
              now: str | None = None, commit: str | None = None, pin: str | None = None,
              cases: dict[str, dict] | None = None) -> list[dict]:
    """Pack-question rows (no chain block; a question written inside a ladder set file) after a
    grading-only change: their stored ``case_revision`` / ``ladder_revision`` no longer equals the
    current case hash, so the board reads them pending. Each stored attempt (bench_run_id, case_id,
    repeat) is rebuilt from the current tree and graded again by the live run's code
    (``runner.grade_single_row``) only when what was SENT is byte-identical (system text and prompt;
    every scripted user turn for a session) and the judged elements are unchanged; otherwise it is
    refused and left stale (re-make it). The superseding rows carry the current case hash and
    ``regraded_by``. A stamp equal to the current hash is current (nothing written)."""
    from . import config as _cfg, degenerate, runner, version
    from .gen import _det
    rules = rules or {}
    wrong_py = _det.pin_mismatch({"grader_pins": {"python": pin}} if pin else None)
    at = now or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    cases = _pack_cases() if cases is None else cases
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get("chain") or r.get("case_id") not in cases:
            continue
        groups.setdefault((r.get("bench_run_id"), r.get("case_id"), r.get("repeat")), []).append(r)
    recs = []
    for (rid, cid, rep_), grp in groups.items():
        grp = sorted(grp, key=_turn)
        raw = cases[cid]
        pk = Path(raw["ladder_pack"])
        key = "/".join(pk.parts[-3:-1])
        if part and key != part:
            continue
        stamps = {version.row_case_stamp(r) for r in grp}
        rec = {"unit": cid, "rung": None, "attempt": None, "bench_run_id": rid, "repeat": rep_, "part": key,
               "set": pk.stem, "family": (raw.get("generator") or {}).get("type"),
               "from_revision": next(iter(stamps)) if len(stamps) == 1 else sorted(map(str, stamps)),
               "score_before": grp[-1].get("score")}
        recs.append(rec)
        cur = version.case_hash(cid)
        if len(stamps) != 1:
            rec.update(status="refused", why=f"the attempt's rows carry different stamps {sorted(map(str, stamps))}")
            continue
        if next(iter(stamps)) == cur:
            rec["status"] = "current"
            continue
        if any(r.get("error") or r.get("skipped") or r.get("grade") == "error" for r in grp):
            rec.update(status="refused", why="an errored attempt (infrastructure): it is re-run, never regraded")
            continue
        why = session_rule_refusal(grp) or effort_refusal(grp)
        if why:
            rec.update(status="refused", why=why)
            continue
        case = copy.deepcopy(raw)
        try:
            if case.get("generator"):
                _cfg._materialize_generated(case)
        except Exception as e:  # noqa: BLE001 - a family that cannot build the question refuses it
            rec.update(status="refused", why=f"the question could not be built: {e}"[:200])
            continue
        if case.get("turns") or case.get("multi_items") or case.get("console"):
            rec.update(status="refused", why="a multi-turn / multi-item pack question: not regraded by this tool yet")
            continue
        why = _compare_sent(grp, case)
        if why:
            rec.update(status="refused", why=f"what was sent differs: {why}")
            continue
        stored_je = next((r.get("judge_elements") for r in reversed(grp) if r.get("judge_elements")), None)
        if (stored_je or case.get("judge_elements")) and stored_je != case.get("judge_elements"):
            rec.update(status="refused", why="judged elements changed: needs a re-judge run, not a restamp")
            continue
        if wrong_py:
            rec.update(status="refused", why=wrong_py)
            continue
        new_rows = copy.deepcopy(grp)
        for r_old, r in zip(grp, new_rows):
            judge = r.get("judge")
            for k in ROW_GRADE_KEYS:
                r.pop(k, None)
            blank = bool(rules.get("empty_stop_rule")) and bool(r.get("empty_stop") or degenerate.is_degenerate(r))
            runner.grade_single_row(r, _result_of(r), case, blank, r.get("max_tokens_sent"))
            if judge is not None:
                r["judge"] = judge
            for k in _PACK_STAMP_KEYS:
                if k in r_old:
                    r[k] = cur
            r["ts"] = at
            r["regraded_by"] = {"tool": "crucibleforge.regrade", "commit": commit, "reason": reason, "at": at,
                                "from_revision": next(iter(stamps)), "to_revision": cur, "pack": True,
                                **({"previous": r_old["regraded_by"]} if r_old.get("regraded_by") else {})}
        rec.update(status="regrade", score_after=new_rows[-1].get("score"), to_revision=cur, rows=new_rows,
                   grade_before=grp[-1].get("grade"), grade_after=new_rows[-1].get("grade"))
    return recs


def _stamp_tiers(rec: dict, grp: list[dict], case: dict, reason: str, at: str, commit, rev_now: str) -> None:
    """``--stamp check_tier``: the tier and domain labels (chains.tag_tiers) on the final row's
    chain elements, from the case shown to be the row's own. Nothing else changes (no score,
    weight, outcome or revision); a stored label that differs from the case's is refused."""
    last = grp[-1]
    els = (last.get("chain") or {}).get("elements")
    if not els:
        rec["status"] = "n/a"
        return
    new_els = chains.tag_tiers(copy.deepcopy(els), case)
    if not any("check_tier" in e for e in new_els):
        rec["status"] = "n/a"             # a family without tiers: untiered
        return
    if new_els == els:
        rec["status"] = "current"
        return
    bad = [f"{e.get('id')} {k} {e.get(k)!r} -> {n.get(k)!r}" for e, n in zip(els, new_els)
           for k in chains.TIER_KEYS if e.get(k) is not None and e.get(k) != n.get(k)]
    if bad:
        rec.update(status="refused", why="the stored labels differ from the case's: " + "; ".join(bad[:3]))
        return
    n_tag = sum(1 for e, n in zip(els, new_els) if e != n)
    new_rows = copy.deepcopy(grp)
    new_rows[-1]["chain"]["elements"] = new_els
    for r_old, r in zip(grp, new_rows):
        r["ts"] = at
        r["regraded_by"] = {"tool": "crucibleforge.regrade", "commit": commit, "reason": reason, "at": at,
                            "stamp": {"check_tier": n_tag}, "from_revision": r_old.get("chain_revision"),
                            "revision_current": r_old.get("chain_revision") == rev_now,
                            "regraded": [], "carried": "all",
                            **({"previous": r_old["regraded_by"]} if r_old.get("regraded_by") else {})}
    rec.update(status="regrade", stamp={"check_tier": n_tag}, to_revision=last.get("chain_revision"),
               rows=new_rows)


def summary(recs: list[dict]) -> list[dict]:
    """Per set: rows identical (regraded), differing (refused), current, mean score before/after."""
    by: dict[tuple, dict] = {}
    for r in recs:
        s = by.setdefault((r.get("part"), r.get("set")), {"part": r.get("part"), "set": r.get("set"),
                                                           "regrade": 0, "refused": 0, "current": 0,
                                                           "n/a": 0, "superseded": 0, "before": [], "after": []})
        s[r["status"]] += 1
        if r["status"] == "regrade":
            s["before"].append(float(r.get("score_before") or 0.0))
            s["after"].append(float(r.get("score_after") or 0.0))
    out = []
    for s in by.values():
        b, a = s.pop("before"), s.pop("after")
        s["score_before"] = round(sum(b) / len(b), 4) if b else None
        s["score_after"] = round(sum(a) / len(a), 4) if a else None
        out.append(s)
    return sorted(out, key=lambda s: (str(s["part"]), str(s["set"])))


def render(recs: list[dict]) -> str:
    lines = ["| part | set | identical (regraded) | differing (refused) | current | n/a | superseded | score before "
             "| after |",
             "|---|---|---|---|---|---|---|---|---|"]
    for s in summary(recs):
        lines.append(f"| {s['part']} | {s['set']} | {s['regrade']} | {s['refused']} | {s['current']} | {s['n/a']} | "
                     f"{s['superseded']} | {s['score_before']} | {s['score_after']} |")
    ref = [r for r in recs if r["status"] == "refused"]
    if ref:
        lines += ["", "Refused (left stale):"]
        lines += [f"- {r['unit']} r{r['rung']} a{r['attempt']} (run {r['bench_run_id']}): {r['why']}" for r in ref]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m crucibleforge.regrade", description=__doc__.split("\n\n")[0])
    ap.add_argument("model", help="model label (transcripts_<label>.jsonl in the results dir)")
    ap.add_argument("--reason", required=True, help="why the grading changed (recorded on every row)")
    ap.add_argument("--profile", help="profile name (default: the rows' own)")
    ap.add_argument("--part", help="only this part, e.g. work/bk_trace")
    ap.add_argument("--switch", choices=SWITCHES, help="the grading switch this regrade is for")
    ap.add_argument("--stamp", choices=STAMPS, help="record this case fact on the rows (no regrade); "
                    "refused unless the case is shown to be the row's own")
    ap.add_argument("--from-tree", type=Path, help="with --stamp pack_location: the checkout the pack rows "
                    "were made from (a path)")
    ap.add_argument("--go", action="store_true", help="append the superseding rows (default: dry run)")
    ap.add_argument("--json", action="store_true", help="print the records (without rows) as JSON")
    a = ap.parse_args(argv)
    rows = config.load_transcripts(a.model)
    if not rows:
        print(f"no transcripts for {a.model} in {config.results_dir()}", file=sys.stderr)
        return 2
    pname = a.profile or next((r.get("profile") for r in reversed(rows) if r.get("profile")), None)
    if not pname:
        print("no profile on the rows: pass --profile", file=sys.stderr)
        return 2
    import os
    if not os.environ.get(profiles.COMPOSITION_ENV):
        # the composition the rows were made under (the run's meta), never the default silently
        meta = config.results_dir() / f"meta_{a.model}.json"
        comp = None
        if meta.is_file():
            try:
                comp = json.loads(meta.read_text(encoding="utf-8")).get("composition")
            except (OSError, ValueError):
                comp = None
        if comp and comp != "default":
            os.environ[profiles.COMPOSITION_ENV] = comp
            print(f"composition {comp} (from {meta.name})", file=sys.stderr)
    from .gen import _det
    cfg = config.load_config()
    prof = profiles.load_profile(pname, cfg)
    cfg = profiles.apply_profile(prof, cfg)[0]   # release seed, speaker_tag_lead, profile defaults
    rules = {"empty_stop_rule": bool((cfg.get("defaults") or {}).get("empty_stop_rule", False))}
    specs = {c["id"]: c for c in chains.chain_cases(prof)}
    if a.stamp and a.switch:
        print("--stamp and --switch are separate passes", file=sys.stderr)
        return 2
    if (a.stamp == "pack_location") != bool(a.from_tree):
        print("--stamp pack_location and --from-tree go together", file=sys.stderr)
        return 2
    recs = (plan_pack_location(rows, a.from_tree, a.reason, commit=_commit()) if a.stamp == "pack_location"
            else plan_stamp(rows, specs, a.stamp, a.reason, a.part, commit=_commit()) if a.stamp
            else plan(rows, specs, a.reason, a.switch, rules, a.part, commit=_commit(),
                      pin=_det.pinned_python(prof))
            # pack questions (no chain block) in the same pass: a grading-only change regrades them too
            + (plan_pack(rows, a.reason, a.part, rules, commit=_commit(), pin=_det.pinned_python(prof))
               if a.switch is None else []))
    if a.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "rows"} for r in recs], indent=1))
    else:
        print(render(recs))
    todo = [r for r in recs if r["status"] == "regrade"]
    if a.go and todo:
        with config._APPEND_LOCK:
            for r in todo:
                for row in r["rows"]:
                    config._append_transcript(a.model, row)
        print(f"\nappended {sum(len(r['rows']) for r in todo)} superseding row(s) for {len(todo)} attempt(s) "
              f"to {config.transcript_path(a.model)}", file=sys.stderr)
    elif todo:
        print(f"\ndry run: {len(todo)} attempt(s) would be regraded; --go to write", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
