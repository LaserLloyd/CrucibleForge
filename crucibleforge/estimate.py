"""Wall-time ESTIMATES for `crucibleforge all --plan` (suite 3.9). No model,
provider or judge is called; every number here is a measured average, so the
plan labels the result "estimate".

Generation, per load round: each (case, repeat) job gets the seconds of its
category from the table below; the jobs are packed longest-first (LPT) onto
the round's parallel slots; the longest slot x CALIBRATION is the round's
wall, plus RELOAD_S for every round after the first on a rig.

    round_min = LPT(job seconds, slots) x 1.2 / 60   (+ 1.5 min per reload)

Slots: a rig round at <= 40,960 ctx gets 8, a wider one 4 (STEP4, omega
31B Q8: 8/6 slots refused at 73,728); a hosted API runs its provider's
concurrency (default 8).

Judge: STEP1 Task 3 / EVAL-FABLE §6 measured 1.4 min WALL per judged row at
samples=3 with the judge at 8 parallel slots (median of 13 phases, range
0.94-1.86 — the slots are already in that figure), plus 1.0-1.6 min for the
judge load and canary:

    judge_min = 1.5 + 1.4 x judged_rows x samples / 3      (0 rows -> 0)

judged_rows = every (case, repeat) with a rubric or multi-turn session.
"""
from __future__ import annotations

import heapq

#: seconds per (case, repeat) job, omega-class rig model (31B Q8 thinker).
#: Source: medians of omega-arsenal-31b's 3.8/3.9 rows in results/
#: (2026-10-28: coding 210, math 391, reasoning 278, instruct 138, tooluse 7,
#: steer 74, rp 44, nsfw 93, story 162); bank tasks from step 2's
#: t2_bank.json (D4 262, B5 364, B6 310, C3 141, E3 252, A8 261 -> ~270);
#: agent tasks ~180 (EVAL §6: F8 65-550 s; stored F1/F3 rows kept only the
#: last turn before S9, so they under-read); RD1 6.5 min (Fable §4);
#: long bank L2/L4 354/411 s. A verbose 27B is about x2 (STEP1 Task 3: T1
#: row time 300 min vs omega's 145).
RIG_SECS = {
    "perf": 10, "coding": 210, "math": 390, "reasoning": 280, "instruct": 140,
    "tooluse": 10, "steer": 75, "rp": 60, "nsfw": 100,
    "story": 160, "rpdeep": 390, "codebank": 270, "codeagent": 180,
    "codebank_long": 380, "codeagent_long": 120,
}
#: seconds per job, hosted API (MiniMax-M3 medians, same source; STEP4: its
#: T1 = 70 rows, 45 row-minutes, 21.6 min wall at concurrency 4).
API_SECS = {
    "perf": 5, "coding": 165, "math": 60, "reasoning": 25, "instruct": 15,
    "tooluse": 5, "steer": 20, "rp": 60, "nsfw": 150,
    "story": 290, "rpdeep": 300, "codebank": 380, "codeagent": 120,
    "codebank_long": 500, "codeagent_long": 60,
}
#: anything not named (a private category among them: ~100-150 s measured)
DEFAULT_SECS = 120
#: actual / simulated generation wall on five 3.8 runs: 0.92-1.27, ~1.2
#: (load, perf probes, imperfect packing) — STEP1 Task 3
CALIBRATION = 1.2
#: the same factor when the job times are the model's own measured ones
MEASURED_CALIBRATION = 1.0
#: a rig model reload between rounds (EVAL §6: "split rounds + 90 s reload")
RELOAD_S = 90
#: rig slots by load context (STEP4)
NARROW_CTX, NARROW_SLOTS, WIDE_SLOTS = 40960, 8, 4
JUDGE_MIN_PER_ROW = 1.4      # at samples=3, judge 8 wide (STEP1 Task 3, EVAL §6)
JUDGE_FIXED_MIN = 1.5        # judge load + canary


#: 4.0 retry pass (bench-all --escalate): per-slot decode at a 64k rung on the
#: rig (wide load), and the share of freshly generated cases expected to run
#: out of budget (3.9 board 2026-10-31: 0-6 of ~35 cases per model, mean ~1.4)
ESCALATION_TOK_S = 40.0
ESCALATION_RATE = 0.04


def escalation_min(jobs_per_model: list[int], rung: int) -> float:
    """Wall minutes of one retry pass: per model a reload, then its jobs at
    ``rung`` tokens (each assumed to use the whole budget) on WIDE_SLOTS."""
    per = float(rung) / ESCALATION_TOK_S
    return sum(RELOAD_S + lpt([per] * int(n), WIDE_SLOTS)
               for n in jobs_per_model if n > 0) * CALIBRATION / 60.0


def rig_slots(ctx: int | None) -> int:
    return NARROW_SLOTS if not ctx or int(ctx) <= NARROW_CTX else WIDE_SLOTS


#: the rpdeep figures above are RD1's: 12 live turns (3.9 step 6: the
#: shorter companion / state-tracking sessions scale by their turn count)
RPDEEP_REF_TURNS = 12


#: the board's output cap (24,576): the budget the category defaults were
#: measured under
BOARD_BUDGET_REF = 24576


def fixed_budget_factor(case: dict) -> float:
    """A case with its own larger ``budget`` (4.0: CL coding and the four
    NSFW writing rows, 65,536) is a long job: its unmeasured time is the
    category default scaled by budget / board cap (never below 1)."""
    try:
        b = int(case.get("ladder_budget") or case.get("budget") or 0)
    except (TypeError, ValueError):
        return 1.0
    return max(1.0, b / BOARD_BUDGET_REF)


def job_seconds(case: dict, rig: bool, measured: dict | None = None) -> float:
    """Seconds one job (a case repeat; a session's turns summed) takes.
    ``measured`` (:func:`measured_speed`): the model's own stored timing —
    the case's measured mean when it has rows, else the category default x
    the model's speed factor."""
    turns = len(case.get("turns") or []) or None
    if measured and case.get("id") in measured["cases"]:
        secs = measured["cases"][case["id"]]
        # a session whose turn count changed since it was measured (4.0
        # merged turns): scale by turns
        n = (measured.get("turns") or {}).get(case["id"])
        return secs * turns / n if turns and n and n != turns else secs
    fam = _family(case.get("id"))
    if measured and turns and fam:
        # an unmeasured session of a measured family (WR2 after WR1): the
        # family's measured seconds per turn x this session's turns
        per = [measured["cases"][cid] / measured["turns"][cid]
               for cid in measured["cases"]
               if _family(cid) == fam and (measured.get("turns") or {}).get(cid)]
        if per:
            return sum(per) / len(per) * turns
    table = RIG_SECS if rig else API_SECS
    secs = float(table.get(case.get("category"), DEFAULT_SECS))
    secs *= fixed_budget_factor(case)
    if case.get("category") == "rpdeep" and case.get("turns"):
        secs *= len(case["turns"]) / RPDEEP_REF_TURNS
    if not measured:
        return secs
    return secs * measured.get("by_cat", {}).get(case.get("category"), measured["factor"])


def _family(case_id: str | None) -> str | None:
    """"WR" for WR1-ninth-string / WR2-red-thread-key; None for ids without one."""
    import re
    m = re.match(r"([A-Za-z]+)\d", case_id or "")
    return m.group(1) if m else None


def measured_speed(label: str, cases: list[dict], rig: bool) -> dict | None:
    """4.0 (planner = measured): {"cases": {case id: mean seconds per job},
    "factor": the model's median measured / default ratio ("by_cat": the same
    per category, used first), "workers": {ctx:
    parallel slots the rig gave it}} from the model's stored rows (turns of a
    session summed; perf, escalation and skipped rows left out). None when
    the model has no timed rows. Measured seconds already include the
    slowdown of running ``workers`` requests at once."""
    import json
    import statistics
    from .config import load_transcripts, results_dir
    try:
        rows = load_transcripts(label)
    except Exception:  # noqa: BLE001 — an estimate never fails the caller
        return None
    jobs: dict[tuple, float] = {}
    nrows: dict[tuple, int] = {}
    from .version import in_regime
    for r in rows:
        if r.get("category") == "perf" or r.get("budget_rung") or r.get("skipped"):
            continue
        # review M4 (secondary): this benchmark, this measurement regime only
        if r.get("profile") not in (None, "bench") or (r.get("bench_revision")
                                                     and not in_regime(r)):
            continue
        t = (r.get("metrics") or {}).get("total_s")
        if not isinstance(t, (int, float)):
            continue
        k = (r.get("bench_run_id"), r.get("case_id"), r.get("repeat"))
        jobs[k] = jobs.get(k, 0.0) + float(t)
        nrows[k] = nrows.get(k, 0) + 1
    per: dict[str, list[float]] = {}
    nturns: dict[str, list[int]] = {}
    for k, t in jobs.items():
        per.setdefault(k[1], []).append(t)
        nturns.setdefault(k[1], []).append(nrows[k])
    if not per:
        return None
    mean = {cid: sum(v) / len(v) for cid, v in per.items()}
    turns = {cid: max(v) for cid, v in nturns.items()}
    by_id = {c["id"]: c for c in cases}
    # the speed factor compares like with like: the measured job against the
    # default for the turn count it was measured with
    def _dflt(cid):
        c = dict(by_id[cid])
        if c.get("turns"):
            c["turns"] = [None] * turns[cid]
        return job_seconds(c, rig)
    ratios = [mean[cid] / _dflt(cid) for cid in mean if cid in by_id]
    by_cat: dict[str, list[float]] = {}
    for cid in mean:
        if cid in by_id:
            by_cat.setdefault(by_id[cid]["category"], []).append(mean[cid] / _dflt(cid))
    workers: dict[int, int] = {}
    try:
        meta = json.loads((results_dir() / f"meta_{label}.json").read_text(encoding="utf-8"))
        for rd in meta.get("rounds") or []:
            if rd.get("ctx") and rd.get("workers"):
                workers[int(rd["ctx"])] = int(rd["workers"])
    except (OSError, ValueError):
        pass
    return {"cases": mean, "turns": turns,
            "factor": statistics.median(ratios) if ratios else 1.0,
            "by_cat": {k: statistics.median(v) for k, v in by_cat.items()},
            "workers": workers}


def lpt(durations: list[float], slots: int) -> float:
    """Makespan of longest-first packing on ``slots`` workers."""
    if not durations:
        return 0.0
    h = [0.0] * max(1, int(slots))
    for d in sorted(durations, reverse=True):
        x = heapq.heappop(h)
        heapq.heappush(h, x + d)
    return max(h)


def generation_min(job_secs: list[float], slots: int, calibration: float = CALIBRATION) -> float:
    return lpt(job_secs, slots) * calibration / 60.0


def judged(case: dict) -> bool:
    return bool(case.get("rubric") or case.get("turns"))


def judge_min(judged_rows: int, samples: int = 3) -> float:
    if judged_rows <= 0:
        return 0.0
    return JUDGE_FIXED_MIN + JUDGE_MIN_PER_ROW * judged_rows * max(1, samples) / 3.0


def row_judge_equiv(row: dict, pcfg: dict | None = None) -> float:
    """One stored row's judge cost in judged-row equivalents at 3 samples
    (the per-row sample policy: a samples=1 row is 1/3)."""
    from .judge import judge_policy
    try:
        return judge_policy(row, pcfg)["samples"] / 3.0
    except Exception:  # noqa: BLE001 — an estimate never fails the caller
        return 1.0


def model_estimate(cfg_p: dict, entry: dict, cases: list[dict], only_jobs: set | None = None,
                   smoke: bool = False, use_measured: bool = False) -> tuple[float, float]:
    """(generation minutes, judged-row equivalents) for running ``cases`` (or
    just ``only_jobs`` of them: {(rid, case id, repeat)}) on ``entry`` — the
    same arithmetic as `all --plan` (load rounds, LPT on the round's slots,
    x CALIBRATION, a reload per extra rig round; judge rows weighted by the
    per-row sample policy). Never calls a model or provider."""
    from .config import repeats_for
    from .judge import judge_policy, judge_policy_config
    from .providers import provider_for
    from .runner import _LOADING_TYPES, plan_rounds
    pcfg = judge_policy_config(cfg_p)
    prov = provider_for(cfg_p, entry)
    rig = prov.type in _LOADING_TYPES
    want = None if only_jobs is None else {(j[1], j[2]) for j in only_jobs}
    gen, judged_eq = 0.0, 0.0
    rounds = plan_rounds(cfg_p, entry, cases, only_jobs)
    measured = None
    if use_measured and entry.get("name"):
        from .config import load_cases
        try:
            have = {x["id"] for x in cases}
            pool = cases + [c for c in load_cases() if c["id"] not in have]
        except Exception:  # noqa: BLE001
            pool = cases
        measured = measured_speed(entry["name"], pool, rig)
    for i, rd in enumerate(rounds):
        secs = []
        pool = rd["only_jobs"]
        for c in rd["cases"]:
            if c.get("category") == "perf":
                continue
            reps = range(1, repeats_for(cfg_p, c["category"], smoke, c) + 1)
            for r in reps:
                if want is not None and (c["id"], r) not in want:
                    continue
                if pool is not None and not any(j[1] == c["id"] and j[2] == r for j in pool):
                    continue
                secs.append(job_seconds(c, rig, measured))
                if judged(c):
                    try:
                        judged_eq += judge_policy({**c, "case_id": c["id"]}, pcfg)["samples"] / 3.0
                    except Exception:  # noqa: BLE001
                        judged_eq += 1.0
        if not secs:
            continue
        slots = rig_slots(rd["ctx"]) if rig else max(1, int(prov.concurrency or 1))
        if rig and measured and measured["workers"].get(int(rd["ctx"] or 0)):
            slots = measured["workers"][int(rd["ctx"])]
        # measured job times already carry the real per-slot speed: the
        # simulated->actual factor applies to the category defaults only
        # (3.9 rounds, 2026-10-31: LPT of measured jobs = actual within 1.11-1.19x
        # with CALIBRATION, within 0.93-1.0x without)
        cal = MEASURED_CALIBRATION if measured else CALIBRATION
        gen += generation_min(secs, slots, cal) + (RELOAD_S / 60 if rig and i else 0)
    return gen, judged_eq


#: the maintainer 2026-10-31: one ladder pack may add at most about this much wall time
#: on a local reference model (generation + its judge)
PACK_LIMIT_MIN = 5.0


def pack_minutes(prof: dict, cases: list[dict], budget: int | None = None,
                 context: int | None = None) -> float:
    """Planner estimate of one ladder pack on the rig at the category defaults
    (the omega-class reference): a reload, its jobs (profile repeats) LPT on
    the slots of its load, x CALIBRATION, plus the judge for its judged rows.
    Its questions run in parallel, so the wall is about the longest question:
    splitting a pack only helps when the judge is the long part."""
    from .judge import judge_policy, judge_policy_config
    pcfg = judge_policy_config(prof)
    reps = (prof or {}).get("repeats") or {}
    crep = (prof or {}).get("case_repeats") or {}
    secs: list[float] = []
    eq = 0.0
    for c in cases:
        if c.get("category") == "perf":
            continue
        n = int(crep.get(c["id"]) or c.get("repeats") or reps.get(c.get("category"), 1))
        secs += [job_seconds(c, True)] * n
        if judged(c):
            try:
                eq += n * judge_policy({**c, "case_id": c["id"]}, pcfg)["samples"] / 3.0
            except Exception:  # noqa: BLE001
                eq += n
    ctx = context or (73728 if int(budget or 0) > 32768 else None)
    # the judge's load + canary (JUDGE_FIXED_MIN) is paid once per judge pass
    # for every model's packs, not per pack
    judge = judge_min(eq, 3) - JUDGE_FIXED_MIN if eq else 0.0
    return RELOAD_S / 60 + generation_min(secs, rig_slots(ctx)) + judge

