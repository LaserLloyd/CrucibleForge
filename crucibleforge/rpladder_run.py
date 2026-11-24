"""The ladder judge pass (``crucibleforge ladder-judge``): places the stored "Role-play: sessions" rows of the given
models on their cards' ladders (crucibleforge/rpladder). A re-judge of stored answers: no model is re-run and no
prompt to any model changes.

Order: (1) the rung check of every ladder that has no cached result for today's ladder, pair prompt and judge
(all three windows; per-ladder files under ``results/rpladder/rungcheck/``); (2) the rows the board counts as
current under the active profile and composition, wave by wave, every comparison in both orders. Verdicts are cached
by the exact request (``results/rpladder/verdicts.jsonl``): a re-run asks nothing it has asked before.

Each placed row is appended to its transcript with ``judge_ladder`` (the judge stage's own append-and-supersede
rule, under the same label locks); its rubric verdict stays. A row with a current, complete placement is skipped.

Judge calls: the profile's judge through the standard loader (judge._load_judge), thinking off, temperature 0,
300 output tokens, the strict pair schema. Errors and rate limits (429) are the shared client's (JudgeClient.chat ->
the provider's retry rules); a call that still fails leaves its cell pending. ``--plan`` loads nothing and calls
nothing: it prints the cache misses it knows of (rung check exact; rows: the first wave exact, an upper bound for
the rest) and the minutes at the measured judge rate.
"""
from __future__ import annotations

import copy
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import rpladder as RL

log = logging.getLogger(__name__)

PART = "chat/j_rpsession"
#: measured judge throughput for these calls (PAIRS 2026-11-15: 55-62 / min on 4 slots)
CALLS_PER_MIN = 55.0


def cache_path(results: Path) -> Path:
    return Path(results) / "rpladder" / "verdicts.jsonl"


def part_windows(prof: dict, override: list[int] | None = None) -> list[int]:
    if override:
        return list(override)
    jm = ((prof.get("chains") or {}).get(PART) or {}).get("judge_mode") or {}
    return list(jm.get("windows") or RL.DEFAULT_WINDOWS) if jm.get("kind") == "ladder" else list(RL.DEFAULT_WINDOWS)


def current_session_rows(label: str, cfg: dict) -> dict[str, dict]:
    """{unit id: newest row} of the label's session rows that the board counts (report.board_filter)."""
    from . import config, report
    raw = config.load_transcripts(label)
    rows = report.board_filter(label, cfg, rows=copy.deepcopy(raw))
    best: dict[str, dict] = {}
    for r in rows:
        ch = r.get("chain") or {}
        if ch.get("part") != PART or r.get("rubric") != "rp_long" or not r.get("conversation"):
            continue
        if ch["id"] not in best or str(r.get("ts")) >= str(best[ch["id"]].get("ts")):
            best[ch["id"]] = r
    return {k: _whole_text(r) for k, r in best.items()}


def _whole_text(x):
    """``x`` with every lone surrogate in its strings replaced by U+FFFD (strings without one are returned as they
    are). A reply cut in the middle of an emoji holds a lone surrogate, which UTF-8 cannot carry: the request key
    and the verdict store of the session judge both failed on it and the sessions stayed pending (2026-11-24).
    Only this in-memory copy changes; the stored row keeps the reply as the model sent it."""
    if isinstance(x, str):
        try:
            x.encode("utf-8")
            return x
        except UnicodeEncodeError:
            return x.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(x, list):
        return [_whole_text(v) for v in x]
    if isinstance(x, dict):
        return {k: _whole_text(v) for k, v in x.items()}
    return x


def _units(rows: dict[str, dict], cards: dict) -> tuple[list[dict], dict]:
    units, by = [], {}
    for uid, r in rows.items():
        u = RL.unit_from_row(r, cards)
        if u is None:
            continue
        u["unit"] = uid
        units.append(u)
        by[(u["card"], u["seed"])] = u

    def other(u):
        for (cid, seed), v in by.items():
            if cid == u["card"] and seed != u["seed"]:
                return v
        return None
    return units, other


def make_judge_fn(jc, stats: dict):
    """requests -> {key: verdict}; one call per request on the judge's slots."""
    from .api import strip_thinking_tags
    from .judge import _NO_THINK
    from .providers import merge_extra_body
    from . import stopflag

    def one(req):
        dl = getattr(jc, "deadline", None)
        if stopflag.ABORT.is_set() or (isinstance(dl, (int, float)) and time.time() >= dl):
            stats["after_deadline"] = stats.get("after_deadline", 0) + 1
            return req["key"], None
        kw = dict(response_format=RL.SCHEMA, max_tokens=RL.CALL["max_tokens"], temperature=RL.CALL["temperature"])
        if getattr(jc, "thinking", False):
            kw["extra_body"] = merge_extra_body(jc.extra_body, _NO_THINK)
        for attempt in (1, 2):
            try:
                res = jc.chat(req["messages"], **kw)
            except Exception as e:  # noqa: BLE001 - one call must not stop the pass; the cell stays pending
                log.warning("ladder: judge call failed (%s): %s", type(e).__name__, str(e)[:200])
                stats["errors"] = stats.get("errors", 0) + 1
                return req["key"], None
            stats["calls"] = stats.get("calls", 0) + 1
            v = RL.parse_verdict(strip_thinking_tags(res.response_text or "").strip())
            if v.get("better") in ("A", "B", "none"):
                return req["key"], {"better": v["better"], "reason": str(v.get("reason") or "")[:300]}
        stats["unparsable"] = stats.get("unparsable", 0) + 1
        return req["key"], None

    def judge(reqs):
        width = max(1, int(getattr(jc, "concurrency", None) or 4))
        with ThreadPoolExecutor(width) as ex:
            return dict(ex.map(one, reqs))
    return judge


def attach_judge(cfg: dict, benched: set, override=None, client: str | None = None):
    """``--attach``: use the judge copy someone else already loaded (the rig operator's instance under its own
    lease): no lease, no load, no unload, nothing released at exit; only chat calls. Refuses unless the serve id is
    resident and ready; concurrency = its live slot count (at most 4)."""
    from . import studioforge
    from .judge import JudgeClient, select_judge
    cand = select_judge(cfg, benched, override=override)
    jc = JudgeClient(cfg, cand)
    if client:
        jc.holder = client          # X-SF-Client on every call = the operator's holder (rig-integration contract)
    p = jc.provider
    if p.type != "studioforge":
        raise RuntimeError(f"ladder --attach: the judge provider {p.name} is not StudioForge")
    live = studioforge.loaded_plan(jc.serve_id, p.base_url, p.api_key, p.mgmt_headers(client_name=jc.holder)) or {}
    if live.get("state") != "ready":
        raise RuntimeError(f"ladder --attach: {jc.serve_id} is not resident and ready on the rig "
                           f"(state {live.get('state')!r}); ask the rig operator to load it")
    jc.concurrency = max(1, min(4, int(live.get("parallel") or 1)))
    log.info("ladder --attach: using resident %s (parallel %s, ctx %s, devices %s, loaded_by %s) as client %s; "
             "no lease taken", jc.serve_id, live.get("parallel"), live.get("ctx_size"), live.get("devices"), live.get("loaded_by"), jc.holder)
    return jc


def plan(cfg: dict, prof: dict, labels: list[str], judge_model: str, windows: list[int]) -> dict:
    from .config import results_dir
    cards = RL.load()
    cache = RL.VerdictCache(cache_path(results_dir()))
    chk_reqs = RL.rung_check_requests(cards, judge_model)
    chk_miss = sum(1 for r in chk_reqs if cache.get(r["key"]) is None)
    chk = RL.load_check(results_dir(), cards, judge_model)
    rows_first = rows_max = n_units = 0
    lads = RL.selected(cards, chk)
    for label in labels:
        units, other = _units(current_session_rows(label, cfg), cards)
        n_units += len(units)
        for u in units:
            lad = lads.get(u["card"])
            ok = RL.usable_axes(chk, lad["key"]) if lad else list(RL.AXES)     # unchecked: every axis (upper bound)
            cells = len(windows) * len(ok)
            rows_first += 2 * cells
            rows_max += 2 * 3 * cells
    total_lo, total_hi = chk_miss + rows_first, chk_miss + rows_max
    out = {"rung_check_calls": chk_miss, "units": n_units, "row_calls_first_wave": rows_first,
           "row_calls_max": rows_max, "calls_low": total_lo, "calls_high": total_hi,
           "minutes_low": round(total_lo / CALLS_PER_MIN, 1), "minutes_high": round(total_hi / CALLS_PER_MIN, 1),
           "windows": windows, "judge_model": judge_model}
    return out


class _OtherJudge(Exception):
    """The judge that loaded is not the profile's: the pass restarts under that judge's own id (D13)."""


def run(cfg: dict, prof: dict, labels: list[str], *, windows: list[int], judge_override=None,
        deadline: float | None = None, rung_check_only: bool = False, force: bool = False,
        attach: bool = False, client: str | None = None) -> dict:
    """The ladder pass. D13 (lead 2026-11-19 b): a judge other than the profile's is accepted. Its verdicts, rung
    check and placements are keyed and stamped with its own id (``instrument.judge_model``), never the profile
    judge's, and a unit already placed by the profile judge is left alone. The board prints such a part as
    "judged by <id>, not the board's judge: not comparable" and never ranks the row; a public render refuses it."""
    j = prof.get("judge") or {}
    board_judge = j.get("model_id") if isinstance(j, dict) else str(j)
    held: dict = {}
    try:
        return _run(cfg, prof, labels, windows=windows, judge_override=judge_override, deadline=deadline,
                    rung_check_only=rung_check_only, force=force, attach=attach, client=client,
                    judge_model=board_judge, board_judge=board_judge, held=held)
    except _OtherJudge:
        other = held["jc"].model_id
        log.warning("ladder: the loaded judge %s is not the profile's %s: its placements are stamped with its own "
                    "id and the board marks them not comparable", other, board_judge)
        out = _run(cfg, prof, labels, windows=windows, judge_override=judge_override, deadline=deadline,
                   rung_check_only=rung_check_only, force=force, attach=attach, client=client,
                   judge_model=other, board_judge=board_judge, held=held)
        out["other_judge"] = other
        return out


def _run(cfg: dict, prof: dict, labels: list[str], *, windows: list[int], judge_override, deadline,
         rung_check_only: bool, force: bool, attach: bool, client, judge_model: str, board_judge: str,
         held: dict) -> dict:
    from .config import append_transcript, results_dir
    from .judge import _load_judge
    from .shard import LabelLocks
    cards = RL.load()
    res_dir = results_dir()
    cache = RL.VerdictCache(cache_path(res_dir))
    stats: dict = {"calls": 0}
    from . import judge45 as _J45
    by_name = {m["name"]: m for m in cfg.get("models") or []}
    # 4.5 (amendment r): the judge is the board's reference model; under test it places its own sessions (noted)
    self_ok = _J45.self_grades(prof)
    benched = set() if self_ok else {by_name[l]["model_id"] for l in labels if l in by_name}
    jc = held.get("jc")

    def judge(reqs):
        nonlocal jc
        if jc is None:
            jc = (attach_judge(cfg, benched, judge_override, client) if attach
                  else _load_judge(cfg, benched, rows=len(reqs), samples=1, override=judge_override))
            if deadline:
                jc.deadline = deadline
            held["jc"] = jc
        if jc.model_id != judge_model:
            # D13: never key another judge's verdicts under this id; nothing has been asked yet
            raise _OtherJudge(jc.model_id)
        return make_judge_fn(jc, stats)(reqs)

    chk = RL.load_check(res_dir, cards, judge_model)
    if set(chk.get("checked") or []) != set(RL.flat(cards)):
        full = RL.rung_check(cards, judge, cache, judge_model=judge_model)
        RL.save_check(res_dir, cards, full, judge_model)
        chk = RL.load_check(res_dir, cards, judge_model)
    out = {"rung_check": {k: v["usable"] for k, v in chk["cells"].items()},
           "rung_check_complete": set(chk["checked"]) == set(RL.flat(cards))}
    if rung_check_only or not out["rung_check_complete"]:
        out.update(stats)
        return out
    locks = LabelLocks(res_dir)
    placed = {}
    try:
        # (not `held`: that name is the judge holder above; rebinding it broke a placement that loads the judge)
        got, locked_out = locks.acquire(labels)
        out["skipped_labels"] = locked_out
        for label in got:
            rows = current_session_rows(label, cfg)
            units, other = _units(rows, cards)
            if not force:
                cur = {}
                for u in units:
                    rec = rows[u["unit"]].get("judge_ladder")
                    # fix round 2026-11-18 (S3): the unit's own replies go in, so a placement that met a
                    # substitute rung is stale here exactly as on the board (chains), and is placed again
                    if (isinstance(rec, dict) and rec.get("complete") and rec.get("windows") == list(windows)
                            and RL.stale_reason(rec, cards, chk, judge_model,
                                                unit_sha=RL.anchor_sha(u["replies"])) is None):
                        cur[u["unit"]] = True
                    elif (judge_model != board_judge and isinstance(rec, dict) and rec.get("complete")
                          and (rec.get("instrument") or {}).get("judge_model") == board_judge):
                        cur[u["unit"]] = True      # D13: placed by the board's judge: another judge never replaces it
                units = [u for u in units if u["unit"] not in cur]
            res = RL.place_units(units, cards, judge, cache, judge_model=judge_model, check=chk,
                                 windows_used=tuple(windows), own_other=other)
            n = 0
            for uid, rec in res.items():
                if not rec.get("complete"):
                    continue
                row = dict(rows[uid])
                if self_ok and (by_name.get(label) or {}).get("model_id") == judge_model:
                    rec = {**rec, "self_graded": True}       # the board prints "graded by itself"
                row["judge_ladder"] = rec
                row["judge_ladder_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                append_transcript(label, row)
                n += 1
            placed[label] = {"placed": n, "pending": sum(1 for r in res.values() if not r.get("complete")),
                             "positions": {u: r.get("position") for u, r in res.items()}}
    finally:
        locks.release()
    out["labels"] = placed
    out.update(stats)
    return out


def summary_lines(out: dict) -> list[str]:
    L = []
    if "rung_check" in out:
        bad = sorted(k for k, v in out["rung_check"].items() if not v)
        L.append(f"rung check: {len(out['rung_check']) - len(bad)}/{len(out['rung_check'])} ladder x axis usable"
                 + ("" if out.get("rung_check_complete") else " (INCOMPLETE: rows not placed)"))
        if bad:
            L.append("  not usable: " + ", ".join(bad))
    for label, d in (out.get("labels") or {}).items():
        ps = [p for p in d["positions"].values() if p is not None]
        L.append(f"{label}: placed {d['placed']}, pending {d['pending']}"
                 + (f", mean position {sum(ps) / len(ps):.2f}" if ps else ""))
    L.append(f"judge calls {out.get('calls', 0)}; errors {out.get('errors', 0)}; unparsable {out.get('unparsable', 0)}")
    return L


def dumps(out: dict) -> str:
    return json.dumps(out, indent=1, default=str)
