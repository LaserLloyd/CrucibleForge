"""Benchmark orchestrator: per-model load → cases → metrics → transcripts.

Reliability rules learned from the v1 draft:
- One bad model must never kill the batch: everything per-model is inside a
  try, and a load failure records the error and moves on.
- Rows are APPENDED to the transcript as they complete (last-row-wins dedupe
  on read), so a killed run keeps everything finished so far.
- The per-case max_tokens is what is actually sent, and what is recorded.
- Model eviction (LM Studio auto-swap / JIT for another client) is detected
  two ways: `lms ps` check before each case, and WrongModelError from the
  response's served-model field. Both trigger one reload + retry.

v3 additions:
- Providers (see providers.py) replace the LM-Studio/StudioForge branching.
- Remote APIs run cases concurrently (``providers.<name>.concurrency``);
  perf cases always run serially first so timing stays honest.
- A case with ``min_context`` larger than the model's context_length is
  recorded as ``skipped`` (not a failure) — long-context tiers beyond a
  small model's window are "not applicable", not "wrong".
- ``cost_usd`` per row from the entry's ``price`` block.
- ``STOP`` event: a cooperative stop flag the GUI can raise between cases.
"""
from __future__ import annotations

import copy
import csv
from pathlib import Path
import json
import logging
import re
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as _fwait
from datetime import datetime, timezone

from . import degenerate, earlystop, lms, rpdeep, session_checks, stopflag, studioforge
from dataclasses import replace as _dc_replace

from .api import (Aborted, ChatResult, GenerationRejected, ProtocolRejected, RequestRejected, RowTimeout,
                  TransportError, WindowClosed, WrongModelError, in_reasoning_flag,
                  reasoning_answer)
from .config import CSV_COLUMNS, results_dir, append_transcript, load_transcripts, repeats_for, transcript_path
from .graders import (_extract_tool_calls_from_text, grade, grade_tool_call, prose_metrics)
from . import spend
from .providers import Provider, cost_usd, merge_extra_body, model_extra_body, provider_for
from .version import judge_fingerprint as _judge_fp, revision as _revision
from .version import private_category_hash
from .stepgrade import grade_answer_step
from . import config as _config
from . import subject_copies as _subject_copies
from . import adult_guard as _adult_guard

log = logging.getLogger(__name__)

CREATIVE_SEEDS = [41, 42, 43, 44, 45]


def writer_seed(case: dict, repeat: int, temperature) -> int:
    """The writer's sampling seed of one repeat: 42 at temperature 0, else creative seed number
    ``repeat`` -- or the case's ``gen_seed`` (rc3: a chain flat set and its seed_as alias ask the
    same instances at two different seeds)."""
    if temperature == 0:
        return 42
    gs = int(case.get("gen_seed") or 0)
    return CREATIVE_SEEDS[((gs or repeat) - 1) % len(CREATIVE_SEEDS)]
SANITY_CHECK_AFTER = 3

# A run aborts (ModelRunError) after this many transport failures IN A ROW —
# the server is gone, not one case. A single failure is recorded as an error
# row for that case and the run continues (one bad case never kills a model).
MAX_CONSECUTIVE_TRANSPORT_ERRORS = 3

# Reasoning-overflow recovery. A thinking model that reaches max_tokens while
# still inside its reasoning channel returns finish=length with EMPTY content:
# nothing to grade, nothing to judge. Measured 2026-08-22 on 251-case runs:
# 27-45 such rows per thinking model (coding/math/planning), i.e. 10-18% of a
# model's score was a budget artifact, not the model's ability. Recovery asks
# for the ANSWER on the case's own budget: first with thinking disabled via
# the chat template (works for qwen3-family templates; a no-op for templates
# that ignore the kwarg), then as a continuation of the truncated reasoning.
# The first attempt's cost is kept on the row (recovery.first) so the
# overflow is still visible in the report.
# reasoning_format the runner requests for a rig model that advertises the
# "thinking" capability (see _Ctx.adopt_rig_reasoning)
RIG_REASONING_FORMAT = "deepseek"
# special-token markers that must never survive into a graded answer:
# `<|channel>`, `<|im_start|>`, `<turn|>` … (not HTML, not `a < b || c`)
_SPECIAL_MARKER = re.compile(r"<\|[A-Za-z_]+\|?>|<[A-Za-z_]+\|>")
RECOVERY_NO_THINK_KWARGS = {"chat_template_kwargs": {"enable_thinking": False}}
# Suite 3.8.0 "as deployed" regime (profile ``regime: deployed``): measure a
# model the way this box runs it. No temperature/top_p is sent (the server's /
# vendor's default sampler applies, as for every production client; a registry
# entry may pin ``sampler: {temperature, top_p}``), the thinking switch is the
# chat template's default, every provider gets the SAME thinking budget (the
# profile's — hosted APIs no longer get a larger one) with a floor
# (``thinking_max_tokens_floor``) so a thinking model is not starved on short
# chat cases, and the profile turns overflow recovery off: a thinking run that
# never reaches an answer scores as that, not as a thinking-off retry at full
# credit (2026-10-24 audit: 71% of rig thinkers' Programs rows were retries).
REGIME_DEPLOYED = "deployed"
RECOVERY_CONTINUE_PROMPT = (
    "Your reasoning above was cut off by the length limit. Do not think "
    "further. Reply now with ONLY your final answer to the original request.")
# how much of the truncated reasoning to feed back on the continuation rung
RECOVERY_REASONING_TAIL_CHARS = 6000
# Escalation ("with room", 2026-10-25). The board budget (24576) is the same
# for every model, so it measures "solves it within 24k" — a long thinker that
# needs 60k on a hard program scores 0 there. After a run, the jobs whose
# answer ran out of budget (finish=length on a thinking model) are re-run on
# the profile's ``escalation_rungs`` (65536, then 131072 for what still runs
# out), the model reloaded with a context wide enough for the rung (fewer
# parallel slots). The rows carry ``budget_rung`` and NEVER count on the board;
# the report's Room column = the board rows with each escalated job replaced by
# its highest-rung run. Maintainer 2026-10-25: "smaller context with an
# increased retry round for anything that doesn't have enough". Only the few
# jobs that ran out pay for the wide context (on 2026-10-25: 1 of 120 jobs for
# one 27B, 21 for another), instead of a whole run at 128k.
DEFAULT_ESCALATION_RUNGS = (65536, 131072)
# prompt + chat-template headroom on top of a rung's output budget (the longest
# multi-turn session prompt is ~6k tokens)
ESCALATION_CTX_PAD = 8192

# Cooperative stop: set() to finish the in-flight case(s) and stop cleanly.
STOP = threading.Event()
# Hard stop (SIGTERM, cli.main): see stopflag.py. Nothing new starts, waits
# end at once, the executor is shut down without joining, the meta is marked
# ``stopped`` and the lease released by the caller's ``finally``.
ABORT = stopflag.ABORT
#: How often the main thread looks at ABORT while workers run (seconds).
POOL_POLL_S = 0.25

# bench-all --until (review B1): ``STOP`` at the window edge lets the rows in
# flight finish; ``GEN_DEADLINE`` (an absolute time.time(), or None) is the
# hard cap on them — a request still running past it is dropped
# (api.WindowClosed), no row is written and the job stays pending.
# ``WINDOW_STOP`` marks a STOP as the window's (not the user's): the model's
# meta then says "stopped at the time window", never FAILED.
GEN_DEADLINE: list[float | None] = [None]
WINDOW_STOP = threading.Event()
# The cooperative drain (lead 2026-11-14): SIGUSR1 or the stop file (cli.py) sets DRAIN and
# STOP: the requests in flight finish, nothing new starts, the meta reads "stopped on request,
# the rest next run" (partial, not FAILED) and the process exits with no judge or board. No
# ceiling by default; ``request_drain(max_minutes=N)`` cuts what is still streaming after N
# minutes (stopflag.CUT) and the run's notes say a stream was cut.
DRAIN = stopflag.DRAIN
DRAIN_NOTE = "stopped on request, the rest next run"
CUT_NOTE = ("a stream was cut by the drain ceiling: unload and reload the model before the "
            "next run")
#: labels whose in-flight request the drain ceiling cut
DRAIN_CUT: set[str] = set()
_DRAIN_TIMER: list = [None]
_DRAIN_LOCK = threading.Lock()


def request_drain(max_minutes: float | None = None, why: str = "request") -> bool:
    """Ask the run to drain: finish what is in flight, start nothing new. ``max_minutes`` (None =
    no ceiling, the default) arms the ceiling once. True when this call started the drain."""
    with _DRAIN_LOCK:
        first = not DRAIN.is_set()
        DRAIN.set()
        STOP.set()
        if max_minutes and max_minutes > 0 and _DRAIN_TIMER[0] is None:
            t = threading.Timer(float(max_minutes) * 60.0, _drain_ceiling, args=(max_minutes,))
            t.daemon = True
            t.start()
            _DRAIN_TIMER[0] = t
    if first:
        log.warning("drain requested (%s): the requests in flight finish, nothing new starts, then "
                    "the process exits (no judge, no board)%s", why,
                    f"; streams still running after {max_minutes:g} min are cut"
                    if max_minutes else "; no ceiling")
    return first


def _drain_ceiling(max_minutes) -> None:
    log.warning("drain ceiling (%g min) reached: cutting the streams still in flight", max_minutes)
    stopflag.CUT.set()


def reset_drain() -> None:
    """Tests / a GUI that runs again in the same process."""
    with _DRAIN_LOCK:
        if _DRAIN_TIMER[0] is not None:
            _DRAIN_TIMER[0].cancel()
            _DRAIN_TIMER[0] = None
        DRAIN.clear()
        stopflag.CUT.clear()
        DRAIN_CUT.clear()

#: Model runs in flight in this process: {label: (entry, provider, cfg)} —
#: what cli's stop watchdog marks ``stopped`` if the process must be ended
#: before the normal path gets there.
ACTIVE: dict[str, tuple] = {}
_ACTIVE_LOCK = threading.Lock()


def check_abort() -> None:
    """Raise RunStopped when a hard stop is pending (between session turns,
    before a job starts)."""
    if ABORT.is_set():
        raise RunStopped("stopped (SIGTERM)")

# Job scheduling order (2026-09-23): multi-turn sessions go first — a 6-turn
# chat session is 6 DEPENDENT generations, the serial long pole of the chat
# half, so it must overlap the long coding rows instead of trailing them —
# then the categories with the longest single generations, so the pool's
# slots are never left idle waiting on one straggling 24k-token coding case
# at the end of the run. Anything not named here runs last, in profile order.
# Suite 3.9 (EVAL-FABLE M7): code-bank rows go FIRST — they are the longest
# single jobs (13-15 min each on a verbose 27B) and used to run last, with
# idle slots, because this list did not name them.
CATEGORY_ORDER = ["codebank", "codeagent", "codebank_long", "codeagent_long",
                  "coding", "math", "reasoning", "rp", "nsfw", "story", "tooluse", "steer",
                  "instruct"]
# categories whose written replies get objective prose metrics
CREATIVE_CATEGORIES = ("rp", "nsfw", "story", "rpdeep")

# Safety net for a STUCK connection: no bytes at all from the server for this
# long ends the row (errored, not failed). It never cuts a model that is still
# producing — a generating model streams continuously. defaults.stall_timeout_s
# overrides; 0 disables.
DEFAULT_STALL_S = 300.0

# A thinking model on this few parallel slots will overrun the time box: the
# long pole is several max-budget cases queued behind each other.
FEW_SLOTS_WARN = 2

# HTTP 4xx that mean "this request is wrong", not "the server is gone": they
# fail the one case and do not count towards the transport-storm abort.
_CASE_LOCAL_REJECT_STATUSES = (400, 413, 422)


def _billing_rejection(e: Exception) -> bool:
    from .preflight import billing_rejection
    return billing_rejection(e)


def _rejection_kind(case: dict, e: Exception) -> str:
    """error_kind of a row stored for a rejected request: a code-bank case's own
    rejection is the model failing ("generation"), but a billing refusal (any
    status) is infra ("transport": excluded, pending, re-queued)."""
    if _billing_rejection(e):
        return "transport"
    return "generation" if case.get("category") in BANK_CATEGORIES else "transport"


# --- ctx_short (4.5): a row cut by the context window is infrastructure, not the model ---
_CTX_ERR_RE = re.compile(
    r"(exceed\w*[^.]{0,60}(context|n_ctx)|(context|n_ctx)[^.]{0,40}(exceed|too (long|small|short)|"
    r"overflow|limit)|maximum context|prompt is too long|available context|context_length_exceeded)",
    re.I)


def is_ctx_short_error(text) -> bool:
    """Whether a server error message says the request did not fit the
    context window."""
    return bool(_CTX_ERR_RE.search(str(text or "")))


def row_ctx_short(row: dict, ctx_len) -> bool:
    """A generated row the CONTEXT window cut short: finish=length although
    the model had budget left (completion < the budget sent) and
    prompt + completion fills the served window. Such a row says nothing about
    the model; it is retried and, failing that, recorded as an errored
    (missing) row, never scored."""
    if not ctx_len or row.get("finish_reason") != "length":
        return False
    m = row.get("metrics") or {}
    p, c = m.get("prompt_tokens"), m.get("completion_tokens")
    if not isinstance(p, (int, float)) or not isinstance(c, (int, float)):
        return False
    if p + c < 0.98 * int(ctx_len):
        return False
    sent = row.get("max_tokens_sent")
    return not (isinstance(sent, (int, float)) and sent and c >= 0.98 * sent)


class ModelRunError(RuntimeError):
    pass


class EarlyStopError(ModelRunError):
    """The early stop (earlystop.py, R3) tripped: this model's run stops, its
    rows stay, ``record`` goes to the meta as ``early_stop`` and the batch
    continues with the next model."""

    def __init__(self, record: dict):
        self.record = record
        super().__init__(earlystop.describe(record))


class RunStopped(RuntimeError):
    pass


def stamp_degenerate(row: dict) -> None:
    """Stamp ``row["degenerate"]`` (degenerate.py: one repeated character or short n-gram)
    so the board, elemstats and the early stop can tell a broken instance from a model's
    wrong answer. Never raises: a row it cannot read is left unstamped."""
    try:
        hit = degenerate.row_degenerate(row)
    except Exception:  # noqa: BLE001 - a stamp must never cost a row
        return
    if hit and not row.get("degenerate"):
        # a session turn stamped at run time keeps its stamp (it says the turn went into
        # history empty)
        row["degenerate"] = hit


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _messages_for(case: dict, turn_history: list[dict] | None = None) -> list[dict]:
    msgs: list[dict] = []
    if case.get("system"):
        msgs.append({"role": "system", "content": case["system"]})
    # rpdeep: a prior chat log (strictly alternating, ending on the assistant)
    # sits between the card and the live turns. It is prompt, not output: the
    # stored ``conversation`` keeps only the live turns (see _run_multiturn).
    if case.get("history"):
        msgs.extend({"role": m["role"], "content": m["content"]} for m in case["history"])
    if turn_history is not None:
        msgs.extend(turn_history)
    else:
        msgs.append({"role": "user", "content": case["prompt"]})
    return msgs


class _Csv:
    """Thread-safe append-only runs.csv writer."""

    def __init__(self):
        results_dir().mkdir(parents=True, exist_ok=True)
        path = results_dir() / "runs.csv"
        exists = path.exists() and path.stat().st_size > 0
        self.f = open(path, "a", newline="", encoding="utf-8")
        self.w = csv.DictWriter(self.f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        self.lock = threading.Lock()
        if not exists:
            self.w.writeheader()

    def write(self, row: dict) -> None:
        with self.lock:
            self.w.writerow(row)
            self.f.flush()

    def close(self):
        self.f.close()


def _metrics_sum(results: list[ChatResult]) -> dict:
    """Metrics of a multi-call row (agent env loop, tool script; S9): time
    and tokens SUMMED over every call, ``n_turns`` = calls made, ttft of the
    first call, tok/s = all completion tokens over all generation time.
    Before 3.9 only the last turn was kept (omega F1: 21 s for 17 turns)."""
    if not results:
        return {}
    if len(results) == 1:
        return {**_metrics(results[0]), "n_turns": 1}

    def tot(attr):
        vals = [getattr(r, attr) for r in results if getattr(r, attr) is not None]
        return sum(vals) if vals else None

    gen, comp = tot("gen_s"), tot("completion_tokens")
    return {**_billed_extra(results), **_cache_split(results),
            "ttft_s": results[0].ttft_s, "gen_s": gen, "total_s": tot("total_s"),
            "prompt_tokens": tot("prompt_tokens"), "completion_tokens": comp,
            "reasoning_tokens": tot("reasoning_tokens"),
            "reasoning_tokens_source": _reasoning_source(results),
            "answer_tokens": _answer_tokens(comp, tot("reasoning_tokens")),
            "tok_per_s": (round(comp / gen, 2) if gen and comp and comp >= 2 else None),
            "prompt_tok_per_s": results[-1].prompt_tok_per_s,
            "n_turns": len(results)}


#: GPUs on the rig; a row's rig share = its cards / RIG_CARDS / slots
RIG_CARDS = 4


def rig_minutes(provider, plan: dict | None, metrics: dict) -> float | None:
    """Rig-minutes a row cost: its wall-clock seconds / 60 x (leased cards / RIG_CARDS) / slots.
    None for a non-rig provider or when the placement or time is unknown."""
    if getattr(provider, "type", None) != "studioforge":
        return None
    devs = (plan or {}).get("devices") or []
    slots = int((plan or {}).get("parallel") or 1)
    secs = metrics.get("total_s")
    if not devs or secs is None:
        return None
    return round(float(secs) / 60.0 * len(devs) / RIG_CARDS / max(1, slots), 4)


def _billed_extra(results: list[ChatResult]) -> dict:
    """Billed-but-discarded tokens (empty-reply retry) over every call; only
    present when non-zero. Used for cost only, never for speed."""
    p = sum(r.billed_extra_prompt_tokens for r in results)
    c = sum(r.billed_extra_completion_tokens for r in results)
    out = {"billed_extra_prompt_tokens": p, "billed_extra_completion_tokens": c} if (p or c) else {}
    cr = sum(getattr(r, "billed_extra_cache_read_tokens", 0) or 0 for r in results)
    cw = sum(getattr(r, "billed_extra_cache_write_tokens", 0) or 0 for r in results)
    if cr or cw:
        out.update(billed_extra_cache_read_tokens=cr, billed_extra_cache_write_tokens=cw)
    return out


def _cache_split(results: list[ChatResult]) -> dict:
    """Prompt-cache split of the rows' ``prompt_tokens`` (native Anthropic,
    2026-11-11): present only when a provider reported it. Cost only —
    ``prompt_tokens`` stays the total input."""
    rd = [r.cache_read_tokens for r in results if getattr(r, "cache_read_tokens", None) is not None]
    wr = [r.cache_write_tokens for r in results if getattr(r, "cache_write_tokens", None) is not None]
    if not rd and not wr:
        return {}
    return {"cache_read_tokens": sum(rd), "cache_write_tokens": sum(wr)}


def _metrics(result: ChatResult) -> dict:
    return {
        **_billed_extra([result]),
        # claude_code only (see claudecode.py): the input/cache split of prompt_tokens
        **{k: getattr(result, k) for k in ("input_tokens", "cache_read_tokens",
                                           "cache_write_tokens") if getattr(result, k, None) is not None},
        **_cache_split([result]),
        "ttft_s": result.ttft_s, "gen_s": result.gen_s, "total_s": result.total_s,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "reasoning_tokens": result.reasoning_tokens,
        "reasoning_tokens_source": getattr(result, "reasoning_tokens_source", None),
        "answer_tokens": _answer_tokens(result.completion_tokens, result.reasoning_tokens),
        "tok_per_s": result.tok_per_s, "prompt_tok_per_s": result.prompt_tok_per_s,
    }


def _answer_tokens(completion: int | None, reasoning: int | None) -> int | None:
    """Visible-answer tokens = completion minus reasoning (OpenAI usage shape: completion includes
    reasoning); None when either is unknown."""
    if completion is None or reasoning is None:
        return None
    return max(0, int(completion) - int(reasoning))


def _reasoning_source(results) -> str | None:
    """"server" when every turn's reasoning count came from the server, "estimated" when any was
    estimated, None when no turn has one."""
    srcs = {getattr(r, "reasoning_tokens_source", None) for r in results} - {None}
    return "estimated" if "estimated" in srcs else ("server" if srcs else None)


#: sampler keys sent in the request body beside temperature/top_p (llama.cpp
#: reads all of them; a hosted API's rule names only the ones it accepts)
SAMPLER_BODY_KEYS = ("top_k", "min_p", "presence_penalty", "frequency_penalty", "repeat_penalty",
                     "dry_multiplier")


def resolve_sampler(cfg: dict, entry: dict) -> tuple[dict, str]:
    """(sampler, source) a model is measured with under the deployed regime.

    The registry entry's own ``sampler:`` wins; else the first rule of the
    profile's ``recommended_samplers`` whose ``match`` regex hits
    ``<provider>:<model_id>`` or the label. A rule is the model maker's (or
    fine-tuner's) published recommendation and carries its ``source`` URL.
    No match = ``({}, "default")``: nothing is sent and the server's own
    defaults apply. Maintainer 2026-10-25: "make sure we're using the
    recommended settings for all models being tested"."""
    import re as _re
    if entry.get("sampler"):
        return dict(entry["sampler"]), "registry"
    key = f"{entry.get('provider', 'studioforge')}:{entry.get('model_id', '')}"
    for rule in (cfg.get("defaults") or {}).get("recommended_samplers") or []:
        rx = rule.get("match")
        if rx and (_re.search(rx, key, _re.IGNORECASE)
                   or _re.search(rx, str(entry.get("name", "")), _re.IGNORECASE)):
            return dict(rule.get("sampler") or {}), str(rule.get("id") or rx)
    return {}, "default"


def protocol_stamp(cfg: dict, entry: dict, provider: Provider, ctx) -> dict:
    """The request protocol a row is measured under -- the conformance
    fingerprint (provider type, reasoning replay, extra_body keys, sampler).

    The same shape the ``conform`` record carries, so a row can be compared
    with the record that says the protocol works for this model: a harness
    change that moves the fingerprint is what makes a row STALE rather than
    merely old (R3).
    """
    from .conformance import protocol_fingerprint
    return protocol_fingerprint(cfg, entry, provider, ctx)


class _Ctx:
    """Everything a single model run needs, shared by the case workers."""

    def __init__(self, cfg, entry, provider: Provider, ctx_len):
        self.cfg = cfg
        self.entry = entry
        self.provider = provider
        self.model_id = entry["model_id"]
        # subject_copies.Placement when the model is served by declared copies (else None)
        self.placement = None
        self.ctx_len = ctx_len
        self.extra_body = model_extra_body(entry)
        # Provider-gated reasoning replay (MiniMax's documented protocol: the
        # assistant turn goes back WITH its reasoning). OFF by default -- a
        # provider without it sends byte-identical requests to before.
        self.reasoning_replay = bool(getattr(provider, "reasoning_replay", False))
        # alignment 2026-11-06 (review B1): a models.yaml row may set its own
        # ``reasoning_replay`` (4.5: the replay_rule record -- the model's own
        # template at default settings, or a card/API rule). It moves
        # only the assistant-turn replay; the provider's request body is
        # untouched (reasoning_split stays a MiniMax-provider matter).
        # 4.5: the value is the replay rule's record (replay_rule: the model's
        # own template at default settings, or a card/API rule)
        from .replay_rule import source as _replay_source, value as _replay_value
        if entry.get("reasoning_replay") is not None:
            self.reasoning_replay = _replay_value(entry)
        self.reasoning_replay_source = (_replay_source(entry) if entry.get("reasoning_replay") is not None
                                        else ("provider-default" if self.reasoning_replay else "default"))
        # claude_code: the explicit effort every request passes and every row records (4.5 default high)
        self.cc_effort = None
        if getattr(provider, "type", None) == "claude_code":
            from .claudecode import effort_for
            self.cc_effort = effort_for(entry)
        self.lock = threading.Lock()
        # serialises reloads (eviction / wrong model) WITHOUT blocking the
        # row writers on ctx.lock for the minutes a load can take (M10)
        self.reload_lock = threading.Lock()

        # Thinking models spend the completion budget on CoT; a case's
        # max_tokens (sized for a direct answer) then truncates them before
        # any answer appears. entry.thinking: true|false|"auto" (default auto:
        # flips to true the first time a reply carries reasoning tokens).
        t = entry.get("thinking", "auto")
        self.thinking: bool | None = None if t == "auto" else bool(t)
        # how ``thinking`` was decided (logged + recorded in the meta, S8)
        self.thinking_source: str | None = None if t == "auto" else "registry"
        # a registry entry that configures a reasoning channel IS a thinking
        # model, whatever a trivial probe says
        if self.thinking is None and self.extra_body.get("reasoning_format"):
            self.thinking = True
            self.thinking_source = "registry reasoning_format"
        # an escalation round's output budget (set by _run_one_model)
        self.rung: int | None = None
        # an escalation round's per-request wall clock (run_plan
        # .escalation_max_wall_s, S10); None = no cap
        self.max_wall_s: float | None = None
        self._probe_pending: str | None = None
        d = cfg.get("defaults", {})
        self.max_output = int(entry.get("max_output_tokens") or 0)
        self.think_factor = float(d.get("thinking_max_tokens_factor", 4))
        self.think_cap = int(d.get("thinking_max_tokens_cap", 32768))
        self.think_floor = int(d.get("thinking_max_tokens_floor") or 0)
        self.regime = d.get("regime")
        # S8 (suite 3.9): one output budget per row for EVERY model under the
        # deployed regime — a model not (yet) seen thinking gets the same
        # board budget as a thinker instead of the raw per-category
        # max_tokens (coding 6144, tooluse 1024, no floor), so a verbose
        # non-thinker, or a thinker the auto-detect missed, is not silently
        # cut on a smaller budget. ``run_plan.one_budget: false`` = old rule.
        self.one_budget = (self.regime == REGIME_DEPLOYED
                           and bool(run_plan(cfg).get("one_budget", True)))
        # code bank (review 2026-10-26, owner: "I just care about results"):
        # every code-bank call gets ONE generous budget, clamped to what fits
        # the model's window next to the prompt; 0 = off (old budget rules)
        self.bank_budget = int(d.get("bank_budget") or 0)
        # the maker's recommended sampler (deployed regime only); the keys
        # beyond temperature/top_p ride in the body (never over a registry
        # extra_body value)
        self.sampler, self.sampler_source = ({}, "case")
        if self.regime == REGIME_DEPLOYED:
            self.sampler, self.sampler_source = resolve_sampler(cfg, entry)
            for k in SAMPLER_BODY_KEYS:
                if self.sampler.get(k) is not None:
                    self.extra_body.setdefault(k, self.sampler[k])
        # The profile's cap (24576) exists because the RIG's 32768 context
        # must hold prompt + output. A remote API with a far larger window
        # (DeepSeek / MiniMax: 1M) may override both knobs per provider in
        # models.yaml — "don't limit models" (deepseek-pro lost 4 Programs
        # rows truncated-empty at 24576 reasoning tokens on 2026-09-24).
        pcfg = ((cfg.get("providers") or {}).get(getattr(provider, "name", None)) or {})
        if (self.regime != REGIME_DEPLOYED
                and getattr(provider, "type", None) not in ("studioforge", "lmstudio")):
            if pcfg.get("thinking_max_tokens_cap"):
                self.think_cap = int(pcfg["thinking_max_tokens_cap"])
            if pcfg.get("thinking_max_tokens_factor"):
                self.think_factor = float(pcfg["thinking_max_tokens_factor"])
        # defaults.reasoning_overflow_recovery (bool, default on); a model
        # entry can opt out with recovery: false
        self.recovery_enabled = (bool(d.get("reasoning_overflow_recovery", True))
                                 and bool(entry.get("recovery", True)))
        # per-category opt-out (profile ``no_recovery``): math recoveries have
        # never produced a pass (0/85), they only cost time
        self.no_recovery = set(d.get("no_recovery") or [])
        # 4.5 (profile ``thinking_fallback``): a reply with no visible content
        # is asked once more with the model's own thinking switch off (call())
        self.thinking_fallback = bool(d.get("thinking_fallback", False))
        # 4.5 (profile ``empty_stop_rule``): a FINISHED reply with no visible content
        # is stamped ``empty_stop`` and goes into a session's history as an empty
        # assistant turn, never as the model's answer (see _empty_stop_stamp)
        self.empty_stop_rule = bool(d.get("empty_stop_rule", False))
        # 4.5 (profile ``on_empty_stop``): what a FINISHED reply with no visible content
        # gets -- ``regenerate`` (the same request once more, settings unchanged, as a
        # user pressing regenerate; session turns included), ``thinking_off`` (= the
        # thinking fallback), ``none`` (no retry). Unset: the older switches decide
        # (thinking_fallback, else the 4.0 plain re-ask on non-chain rows).
        oes = d.get("on_empty_stop")
        if oes not in (None, "regenerate", "regenerate_then_thinking_off", "thinking_off", "none"):
            raise ValueError(f"on_empty_stop: {oes!r} (regenerate | regenerate_then_thinking_off"
                             " | thinking_off | none)")
        self.on_empty_stop = oes
        if oes == "thinking_off":
            self.thinking_fallback = True
        elif oes in ("regenerate", "regenerate_then_thinking_off", "none"):
            self.thinking_fallback = False
        # flips to False the first time the provider rejects chat_template_kwargs
        self.no_think_supported = True
        self.stall_s = float(d.get("stall_timeout_s", DEFAULT_STALL_S) or 0) or None
        # tripwire for a thinking dialect nobody parses yet (see _observe)
        self.unparsed_markers = 0
        self.marker_example: str | None = None

    def adopt_rig_reasoning(self) -> str | None:
        """Ask the rig to separate reasoning for every thinking model.

        StudioForge launches models with ``--reasoning-format none`` by
        default (its D12), so thoughts arrive INLINE in ``content`` and the
        bench only coped when it happened to know the model's tag dialect.
        gemma4's ``<|channel>thought`` was unknown on 2026-10-24: tags leaked,
        no x4 budget, ~60% of rows truncated, three models scored 0. The rig
        advertises ``capabilities: ["thinking"]`` per model, so the request
        carries ``reasoning_format`` whatever the architecture or dialect.
        An explicit registry value (even ``none``) wins;
        ``defaults.rig_reasoning_format: null`` switches this off. Returns
        the format adopted, or None."""
        fmt = (self.cfg.get("defaults") or {}).get("rig_reasoning_format", RIG_REASONING_FORMAT)
        if (not fmt or getattr(self.provider, "type", None) != "studioforge"
                or "reasoning_format" in self.extra_body):
            return None
        try:
            caps = self.provider.model_capabilities(self.model_id)
        except Exception as e:  # noqa: BLE001 — an unreadable listing changes nothing
            log.warning("%s: rig capabilities unavailable (%s) — reasoning_format "
                        "left to the registry", self.model_id, e)
            return None
        if "thinking" not in caps:
            return None
        self.extra_body["reasoning_format"] = fmt
        if self.thinking is None:
            self.thinking = True
            self.thinking_source = "rig capabilities (thinking)"
        log.info("%s: rig says thinking model — requesting reasoning_format=%s "
                 "(thinking budget %s)", self.model_id, fmt,
                 "on" if self.thinking else "off by registry")
        return fmt

    def marker_warning(self, rows: int) -> str | None:
        """The meta warning for answers that still carried special-token
        markers, or None. Such scores measure a parsing gap, not the model."""
        if not self.unparsed_markers:
            return None
        return (f"{self.unparsed_markers} of {rows} answer(s) carried unparsed special-token "
                f"markers (e.g. {self.marker_example!r}) — likely a thinking/channel dialect "
                f"neither the rig nor the bench separates; this model's scores are suspect "
                f"(teach api._THINK_TAGS the dialect or set extra_body.reasoning_format)")

    def regen_for(self, case: dict) -> bool:
        """Whether an empty stop may be regenerated (on_empty_stop: regenerate): every
        row but a timing row -- session turns too (an empty reply is not running out)."""
        return case["category"] != "perf"

    def recover_for(self, case: dict) -> bool:
        """Whether reasoning-overflow recovery applies to this case."""
        # a chain attempt is never re-asked with thinking off: running out IS its outcome (R23)
        return (case["category"] != "perf" and case["category"] not in self.no_recovery
                and not case.get("chain"))

    def budget(self, max_tokens: int) -> int:
        """max_tokens to actually send for this model: case budget x factor
        within [floor, cap] for a thinking model — and for every model under
        the one-budget rule (S8); else the case's own max_tokens."""
        if self.thinking or getattr(self, "one_budget", False):
            return int(min(self.think_cap, max(max_tokens, max_tokens * self.think_factor,
                                               self.think_floor)))
        return int(max_tokens)

    def sampling(self, case: dict) -> tuple[float | None, float | None]:
        """(temperature, top_p) to send for a case. The deployed regime sends
        the model's recommended values (:func:`resolve_sampler`; None =
        omitted from the request); older regimes use the case's own values."""
        if self.regime == REGIME_DEPLOYED:
            return self.sampler.get("temperature"), self.sampler.get("top_p")
        return case.get("temperature", 0.0), case.get("top_p", 1.0)

    def regime_stamp(self) -> dict:
        """What the measurement ran under — stamped on every row and the meta,
        so rows of different regimes can never be mistaken for each other."""
        return {"regime": self.regime or "fixed",
                "reasoning_format": self.extra_body.get("reasoning_format"),
                "thinking": self.thinking,
                "sampler": ({**self.sampler, "source": self.sampler_source}
                            if self.sampler else self.sampler_source)
                if self.regime == REGIME_DEPLOYED else "case",
                **({"cc_effort": self.cc_effort} if self.cc_effort else {})}

    def _observe(self, result: ChatResult) -> None:
        m = _SPECIAL_MARKER.search(result.response_text or "")
        if m:
            with self.lock:
                self.unparsed_markers += 1
                if self.marker_example is None:
                    self.marker_example = m.group(0)
                    log.warning("%s: unparsed special-token marker %r in an answer — "
                                "a thinking dialect is leaking", self.model_id, m.group(0))
        if self.thinking is None and (result.reasoning_tokens or result.reasoning_text):
            log.info("%s emits reasoning tokens — treating it as a thinking model "
                     "(max_tokens ×%g, cap %d)", self.model_id, self.think_factor, self.think_cap)
            self.thinking = True
            self.thinking_source = self._probe_pending or "a reply carried reasoning"

    def detect_thinking(self) -> None:
        """One probe so ``thinking`` is settled before concurrent cases start
        (otherwise the first few would run on the un-boosted budget). The
        probe asks for a little arithmetic so a reasoning channel, if the
        model has one, actually shows up. A NEGATIVE probe does not settle
        anything: auto-detect stays armed and flips on the first reply that
        carries reasoning (gemma-e4b answered a trivial probe without
        reasoning and then overflowed every hard case on the raw budget)."""
        if self.thinking is not None:
            return
        self._probe_pending = "probe carried reasoning"
        try:
            r = self.provider.chat(
                self.model_id,
                [{"role": "user", "content": "What is 17 * 23 + 4? Work it out, then "
                                             "give the number."}],
                # the run's own sampler: kimi-k3 refuses any temperature but 1 (2026-10-25)
                max_tokens=256, seed=42, extra_body=self.extra_body,
                **({"cc_effort": self.cc_effort} if self.cc_effort else {}),
                **dict(zip(("temperature", "top_p"), self.sampling({}))))
        except (TransportError, WrongModelError) as e:
            log.warning("thinking probe failed (%s) — will auto-detect from replies", e)
            self._probe_pending = None
            return
        self._observe(r)
        self._probe_pending = None
        if self.thinking is None:
            log.info("%s: probe showed no reasoning channel — auto-detect stays armed",
                     self.model_id)

    def thinking_record(self) -> dict:
        """The thinking auto-detect decision for the meta (S8): the value, how
        it was reached, and the budget rule it fed."""
        return {"thinking": self.thinking,
                "source": self.thinking_source or ("no reasoning seen" if self.thinking is None
                                                   else "unknown"),
                "registry": self.entry.get("thinking", "auto"),
                "one_budget": self.one_budget}

    def _chat(self, messages, **kw) -> ChatResult:
        """One completion; on eviction/wrong-model, reload once and retry."""
        if self.stall_s:
            kw.setdefault("stall_s", self.stall_s)
        if self.max_wall_s:
            kw.setdefault("max_wall_s", self.max_wall_s)
        if GEN_DEADLINE[0]:
            kw.setdefault("deadline", GEN_DEADLINE[0])
        # models.yaml ``max_output_tokens``: the vendor's hard output ceiling (e.g. 64000 on some
        # Claude models, which 400 the whole request above it); budgets are clamped, never refused
        if self.max_output and kw.get("max_tokens") and kw["max_tokens"] > self.max_output:
            kw["max_tokens"] = self.max_output
        if self.cc_effort:
            kw["cc_effort"] = self.cc_effort
        held = _subject_copies.current()
        sid = held.serve_id if held is not None else self.model_id
        try:
            r = self.provider.chat(sid, messages, **kw)
        except WrongModelError:
            if self.placement is not None:
                raise    # declared copies never reload through the provider (run_case_repeat)
            log.warning("%s — reloading and retrying once", e)
            check_abort()
            with self.reload_lock:
                self.provider.switch_model(self.model_id, self.ctx_len)
            r = self.provider.chat(self.model_id, messages, **kw)
        self._observe(r)
        return r

    @staticmethod
    def _answered(r: ChatResult) -> bool:
        """Content OR a tool call is an answer (a tool-use reply legitimately
        has empty content)."""
        return bool(r.response_text.strip() or r.tool_calls)

    @classmethod
    def _overflowed(cls, r: ChatResult) -> bool:
        """finish=length with no answer but a reasoning channel: the whole
        budget went to thinking."""
        return (r.finish_reason == "length" and not cls._answered(r)
                and bool(r.reasoning_text.strip()))

    @classmethod
    def _stopped_without_answer(cls, r: ChatResult) -> bool:
        """finish=stop with no answer at all. Measured 2026-09-24: joyfox-35b
        opens <think>, writes its whole reply inside and ends the message
        without </think> on every session turn >= 2 — streamed with
        reasoning_format=deepseek, the reply is all reasoning_content and
        content is empty (llama.cpp's NON-streamed parse returns the same
        text as content, so the server itself does not call it thinking);
        deepseek-pro NMX1 t6 drafted in its reasoning and stopped. The reply
        is asked for again (thinking off, then continuation) — the reasoning
        text is never taken as the answer HERE: nothing distinguishes a reply
        from real chain-of-thought (deepseek's starts "We need answer as…").
        Only when asking again fails (or is off) does the row fall back to
        the reasoning channel, stamped answered_in_reasoning — the one policy
        every path shares (api.reasoning_answer, runner.reply_of)."""
        return r.finish_reason == "stop" and not cls._answered(r)

    def _empty_stop_chain(self, messages, first: ChatResult, budget: int, kw: dict,
                          mode: str) -> ChatResult:
        """The 4.5 empty-stop retries (``on_empty_stop``). ``regenerate``: the same request
        once more, nothing changed (a stray end-of-turn mid-reasoning). ``regenerate_then_
        thinking_off``: if that retry is ALSO empty with its reply left in the reasoning
        channel, one more attempt with the model's own thinking switch off (a template that
        traps the reply in the block). At most two extra requests. The first visible reply
        is returned (graded, into history); every attempt is recorded on it as
        ``empty_stop_retry {mode, chain: [{mode, completion_tokens, reasoning_chars,
        reasoning_tail, visible[, switch, rejected]}], graded: index | None, retry_visible}``.
        No visible reply: the last attempt is returned (graded None -> empty_stop)."""
        def visible(r):
            return bool((r.response_text or "").strip() or r.tool_calls)

        def entry(r, m, **extra):
            rt = r.reasoning_text or ""
            return {"mode": m, "completion_tokens": r.completion_tokens, "reasoning_chars": len(rt),
                    "reasoning_tail": rt[-60:], "visible": visible(r), **extra}

        first_seed = kw.get("seed")

        def seeded(k, attempt):
            # a FRESH seed per retry (a seeded llama-server request repeats its reply), the
            # same on every re-run of the case; every other field unchanged
            return {**k, "seed": retry_seed(first_seed, attempt)} if first_seed is not None else dict(k)

        attempts = [first]
        chain = [entry(first, "first", seed=first_seed)]
        log.warning("%s: empty reply with finish=stop — regenerating once", self.model_id)
        rkw = seeded(kw, 1)
        cur = self._chat(messages, max_tokens=budget, **rkw)
        attempts.append(cur)
        chain.append(entry(cur, "regenerate", seed=rkw.get("seed")))
        # Jake 2026-11-14: "regenerate, then thinking off if still empty" -- any empty stop
        # (reply left in the reasoning channel, or nothing at all); never a degenerate one
        if (mode == "regenerate_then_thinking_off" and not visible(cur) and _empty_stop(cur)
                and not _degenerate_result(cur)):
            log.warning("%s: regenerate also empty, reply in the reasoning channel — "
                        "one more attempt with thinking off", self.model_id)
            switch, rejected = None, False
            if getattr(self, "no_think_supported", True):
                fb_kw = {**seeded(kw, 2), "extra_body": merge_extra_body(kw.get("extra_body"),
                                                                         RECOVERY_NO_THINK_KWARGS)}
                try:
                    cur = self._chat(messages, max_tokens=budget, **fb_kw)
                    switch = "enable_thinking=false"
                except RequestRejected as e:
                    if _billing_rejection(e):
                        raise
                    log.warning("%s: provider rejects chat_template_kwargs (%s) — no third attempt",
                                self.model_id, str(e)[:120])
                    self.no_think_supported = False
                    rejected = True
            else:
                rejected = True
            if rejected:
                # no switch to send: a third identical request would be another regenerate
                chain.append({"mode": "thinking_off", "switch": None, "rejected": True,
                              "visible": False, "completion_tokens": None, "reasoning_chars": 0,
                              "reasoning_tail": "", "seed": None})
            else:
                attempts.append(cur)
                chain.append(entry(cur, "thinking_off", switch=switch, seed=fb_kw.get("seed")))
        graded = next((i for i, a in enumerate(attempts) if visible(a)), None)
        out = attempts[graded] if graded is not None else attempts[-1]
        out.empty_retry = True
        out.empty_stop_retry = {"mode": mode, "chain": chain,
                                "graded": (chain.index(next(c for c in chain if c["visible"]))
                                           if graded is not None else None),
                                "retry_visible": graded is not None,
                                "first_seed": first_seed, "retry_seed": rkw.get("seed"),
                                "answered_thinking_off": (graded is not None
                                                          and chain[next(i for i, c in enumerate(chain)
                                                                         if c["visible"])]["mode"]
                                                          == "thinking_off"),
                                # 4.5 regenerate fields kept for readers of the first build
                                "first_completion_tokens": first.completion_tokens,
                                "first_reasoning_chars": len(first.reasoning_text or ""),
                                "first_reasoning_tail": (first.reasoning_text or "")[-60:]}
        # every other attempt was billed too: billed-only extras (cost), never in
        # prompt/completion_tokens (they feed tok/s)
        for a in attempts:
            if a is out:
                continue
            out.billed_extra_prompt_tokens += (a.prompt_tokens or 0) + a.billed_extra_prompt_tokens
            out.billed_extra_completion_tokens += ((a.completion_tokens or 0)
                                                   + a.billed_extra_completion_tokens)
            out.billed_extra_cache_read_tokens += ((a.cache_read_tokens or 0)
                                                   + a.billed_extra_cache_read_tokens)
            out.billed_extra_cache_write_tokens += ((a.cache_write_tokens or 0)
                                                    + a.billed_extra_cache_write_tokens)
        return out

    def call(self, messages, *, max_tokens: int, recover: bool = True, **kw) -> ChatResult:
        """One completion on the CASE budget ``max_tokens`` (boosted for a
        thinking model), with recovery for a reply that never reached the
        content channel (reasoning overflow, or a stop inside the reasoning
        block). ``recover=False`` for timing rows (perf), whose metrics must
        stay single-shot."""
        kw.setdefault("extra_body", self.extra_body)
        exact = kw.pop("exact_budget", None)
        regen = kw.pop("regen", recover)
        first = self._chat(messages, max_tokens=exact or self.budget(max_tokens), **kw)
        # (under a profile with the old thinking-off recovery, that recovery
        # handles a stop without an answer instead)
        fallback_on = bool(getattr(self, "thinking_fallback", False))
        oes = getattr(self, "on_empty_stop", None)
        if oes == "none":
            pass
        elif oes in ("regenerate", "regenerate_then_thinking_off"):
            # a degenerate reply (degenerate.py) is never regenerated: it is a failure, graded
            # as a blank (lead 2026-11-14); the degenerate test comes first, so a row never
            # carries a regenerate of a degenerate attempt (the chain applies the same test
            # to the regenerate before any thinking-off attempt)
            if regen and _empty_stop(first) and not _degenerate_result(first):
                first = self._empty_stop_chain(messages, first, exact or self.budget(max_tokens), kw, oes)
        elif recover and not self.recovery_enabled and not fallback_on and _empty_stop(first):
            # 4.0 (validation item 10): a reply that comes back EMPTY with
            # finish=stop — no content, no tool call — is a serving hiccup,
            # not an answer: the same request is sent ONCE more (budget,
            # sampler and thinking unchanged). A second empty reply stands.
            # (profiles without ``thinking_fallback``: bench 4.1)
            log.warning("%s: empty reply with finish=stop — retrying once", self.model_id)
            again = self._chat(messages, max_tokens=exact or self.budget(max_tokens), **kw)
            again.empty_retry = True
            # the first call was billed too: carry its tokens as billed-only
            # extras (cost), NOT in prompt/completion_tokens (they feed tok/s)
            again.billed_extra_prompt_tokens = ((first.prompt_tokens or 0)
                                                + first.billed_extra_prompt_tokens)
            again.billed_extra_completion_tokens = ((first.completion_tokens or 0)
                                                    + first.billed_extra_completion_tokens)
            again.billed_extra_cache_read_tokens = ((first.cache_read_tokens or 0)
                                                    + first.billed_extra_cache_read_tokens)
            again.billed_extra_cache_write_tokens = ((first.cache_write_tokens or 0)
                                                     + first.billed_extra_cache_write_tokens)
            first = again
        elif recover and not self.recovery_enabled and fallback_on and (_empty_stop(first) or _capped_empty(first)):
            # Thinking fallback (2026-11-14, maintainer: "switch off thinking
            # ... when a reply doesn't exit thinking ... I only want to grade
            # responses given"). A reply with NO visible content -- it
            # finished inside its thinking block (answer filed as reasoning),
            # ran to the cap without closing it, or came back empty -- is
            # asked ONCE more, the same request with the model's own
            # non-thinking switch (chat_template_kwargs enable_thinking=false;
            # a template without the switch ignores it, so the retry is the
            # plain re-ask of 4.0 validation item 10). The reply that comes
            # back is graded; a second empty reply stands (blank). Same for
            # every model; the row says what happened (thinking_fallback).
            what = ("ran_to_cap" if first.finish_reason == "length" else
                    "misrouted" if (first.reasoning_text or "").strip() else "empty")
            log.warning("%s: no visible reply (%s, %s tokens) — asking once more with thinking off",
                        self.model_id, what, first.completion_tokens)
            budget = exact or self.budget(max_tokens)
            fb_kw, switch = dict(kw), None
            if getattr(self, "no_think_supported", True):
                fb_kw = {**kw, "extra_body": merge_extra_body(kw.get("extra_body"),
                                                              RECOVERY_NO_THINK_KWARGS)}
                switch = "enable_thinking=false"
            try:
                again = self._chat(messages, max_tokens=budget, **fb_kw)
            except RequestRejected as e:
                if switch is None or _billing_rejection(e):
                    raise
                log.warning("%s: provider rejects chat_template_kwargs (%s) — plain re-ask",
                            self.model_id, str(e)[:120])
                self.no_think_supported = False
                switch = None
                again = self._chat(messages, max_tokens=budget, **kw)
            again.empty_retry = True
            again.thinking_fallback = {
                "first": what, "first_finish": first.finish_reason,
                "first_completion_tokens": first.completion_tokens,
                "first_reasoning_chars": len(first.reasoning_text or ""),
                "switch": switch,
                "fallback_visible": bool((again.response_text or "").strip() or again.tool_calls)}
            # the first call was billed too: carry its tokens as billed-only
            # extras (cost), NOT in prompt/completion_tokens (they feed tok/s)
            again.billed_extra_prompt_tokens = ((first.prompt_tokens or 0)
                                                + first.billed_extra_prompt_tokens)
            again.billed_extra_completion_tokens = ((first.completion_tokens or 0)
                                                    + first.billed_extra_completion_tokens)
            again.billed_extra_cache_read_tokens = ((first.cache_read_tokens or 0)
                                                    + first.billed_extra_cache_read_tokens)
            again.billed_extra_cache_write_tokens = ((first.cache_write_tokens or 0)
                                                     + first.billed_extra_cache_write_tokens)
            first = again
        cause = ("overflow" if self._overflowed(first) else
                 "stopped_in_reasoning" if self._stopped_without_answer(first) else None)
        if not (recover and self.recovery_enabled and cause):
            return first
        first_info = {"finish_reason": first.finish_reason,
                      "completion_tokens": first.completion_tokens,
                      "reasoning_tokens": first.reasoning_tokens,
                      "reasoning_chars": len(first.reasoning_text),
                      "max_tokens_sent": self.budget(max_tokens)}
        log.info("%s: %s (%s reasoning tokens, no answer) — recovering the answer on a "
                 "%d-token budget", self.model_id,
                 "reasoning overflow" if cause == "overflow" else "stopped inside reasoning",
                 first.reasoning_tokens or f"~{len(first.reasoning_text) // 4}", max_tokens)
        attempts = 0
        spent = [first]  # every attempt's tokens are billed on the row
        # rung 1: same conversation, thinking disabled via the chat template
        no_think = dict(kw)
        r = None
        error = None
        if self.no_think_supported:
            no_think = {**kw, "extra_body": merge_extra_body(kw.get("extra_body"),
                                                             RECOVERY_NO_THINK_KWARGS)}
            attempts += 1
            try:
                r = self._chat(messages, max_tokens=max_tokens, **no_think)
                spent.append(r)
            except RequestRejected as e:
                if _billing_rejection(e):
                    raise
                # hosted API that validates request fields — remember, and
                # fall through to the continuation rung without the kwarg
                log.warning("%s: provider rejects chat_template_kwargs (%s) — "
                            "continuation-only recovery from now on", self.model_id,
                            str(e)[:120])
                self.no_think_supported = False
                no_think = dict(kw)
            except (TransportError, WrongModelError) as e:
                if isinstance(e, (Aborted, WindowClosed)):
                    raise           # stopping: no row (the job stays pending)
                # a failed RECOVERY request must not throw away the honest
                # first result nor count as a transport failure of the case
                error = f"no_think: {str(e)[:160]}"
                log.warning("%s: recovery request failed (%s)", self.model_id, error)
        mode = "no_think"
        if (error is None and first.reasoning_text.strip()
                and (r is None or not self._answered(r))):
            # rung 2: the template ignored the kwarg (or the model thinks
            # anyway) — continue from the truncated reasoning and ask for
            # the answer outright
            tail = first.reasoning_text[-RECOVERY_REASONING_TAIL_CHARS:]
            cont = list(messages) + [
                {"role": "assistant", "content": tail},
                {"role": "user", "content": RECOVERY_CONTINUE_PROMPT}]
            attempts += 1
            try:
                r = self._chat(cont, max_tokens=max_tokens, **no_think)
                spent.append(r)
                mode = "continue"
            except (TransportError, WrongModelError) as e:
                if isinstance(e, (Aborted, WindowClosed)):
                    raise
                error = f"continue: {str(e)[:160]}"
                log.warning("%s: recovery request failed (%s)", self.model_id, error)
                r = None
        if r is None or not self._answered(r):
            # unrecoverable: keep the honest first result, annotated
            first.recovery = {"mode": None, "attempts": attempts, "first": first_info,
                              "answer_max_tokens": max_tokens, "cause": cause}
            if error:
                first.recovery["error"] = error
            log.warning("%s: reasoning overflow NOT recovered after %d attempt(s)",
                        self.model_id, attempts)
            self._bill(first, spent)
            return first
        r.recovery = {"mode": mode, "attempts": attempts, "first": first_info,
                      "answer_max_tokens": max_tokens, "cause": cause}
        # the thinking cost is part of the model's behaviour — keep it visible
        # in the metrics even though the answer came from the recovery pass
        if first.reasoning_tokens and not r.reasoning_tokens:
            r.reasoning_tokens = first.reasoning_tokens
        self._bill(r, spent)
        return r

    @staticmethod
    def _bill(final: ChatResult, attempts: list[ChatResult]) -> None:
        """Token/time totals over every attempt (cost + budgets are honest);
        ttft/gen/tok_per_s stay those of the pass that produced the answer."""
        final.recovery["attempts_tokens"] = [
            {"prompt_tokens": a.prompt_tokens, "completion_tokens": a.completion_tokens,
             "finish_reason": a.finish_reason} for a in attempts]
        ptoks = [a.prompt_tokens for a in attempts if a.prompt_tokens is not None]
        ctoks = [a.completion_tokens for a in attempts if a.completion_tokens is not None]
        if ptoks:
            final.prompt_tokens = sum(ptoks)
        if ctoks:
            final.completion_tokens = sum(ctoks)
        for attr in ("cache_read_tokens", "cache_write_tokens"):
            vals = [getattr(a, attr) for a in attempts if getattr(a, attr, None) is not None]
            if vals:
                setattr(final, attr, sum(vals))
        final.total_s = sum(a.total_s or 0.0 for a in attempts)


def _sanity_check(state: dict, rows: list[dict], label: str) -> None:
    """If the first N completions are all empty the model is misconfigured:
    stop burning hours on it. Skipped / errored rows are not completions, and
    neither is a reply the provider blocked (finish_reason content_filter):
    that is the provider's decision, not a broken model."""
    for row in rows:
        if row.get("skipped") or row.get("error"):
            continue  # not a completion — counted by the transport guard
        blocked = row.get("finish_reason") == "content_filter"
        if (state.get("block_guard") and row.get("turn") in (None, 1)
                and state.get("sanity_seen", 0) < SANITY_CHECK_AFTER):
            # the first N completions of any kind: all blocked = a provider
            # that refuses everything (a wrong key tier, a region), not a run
            state["sanity_seen"] = state.get("sanity_seen", 0) + 1
            state["sanity_blocked"] = state.get("sanity_blocked", 0) + (1 if blocked else 0)
            if (state["sanity_seen"] == SANITY_CHECK_AFTER
                    and state["sanity_blocked"] == SANITY_CHECK_AFTER):
                raise ModelRunError(f"first {SANITY_CHECK_AFTER} replies all blocked by the "
                                    f"provider's content filter for {label}")
        if blocked:
            continue
        if row.get("turn") in (None, 1):
            state["sanity_total"] += 1
            if not (row.get("response") or row.get("reasoning") or row.get("tool_calls")):
                state["sanity_empty"] += 1
    if (state["sanity_total"] >= SANITY_CHECK_AFTER
            and state["sanity_empty"] == state["sanity_total"]):
        raise ModelRunError(f"first {state['sanity_total']} completions all empty for {label}")


def run_models(cfg: dict, model_entries: list[dict], cases: list[dict],
               smoke: bool = False, jobs_by_label: dict | None = None,
               rung: int | None = None, min_ctx: int | None = None,
               record_key: str | None = None,
               skip_conformance: bool = False,
               bench_run_ids: dict | None = None, stage: str | None = None) -> dict:
    """jobs_by_label: optional {label: {(bench_run_id, case_id, repeat), ...}}
    restricting each model to exactly those (case, repeat) jobs, re-run under
    their ORIGINAL bench_run_id so the new rows supersede the old ones (used
    by ``crucibleforge recover``). ``rung``: an escalation round — the jobs
    (bench_run_id None) run on that output budget under a NEW bench_run_id and
    their rows carry ``budget_rung`` (see DEFAULT_ESCALATION_RUNGS).
    ``min_ctx``: load each model at least at this context (a tier-3 round,
    tiers.py). ``record_key``: the meta list a jobs round is recorded under
    (default "escalated" for a rung, else "recovered"; a tier round: "tiers").
    ``bench_run_ids``: {label: id} continues that run (its rows and meta) instead of a new
    one (a routed run's locator and module stages are ONE run: routing.py)."""
    if not DRAIN.is_set():
        # a new phase starts with a clean STOP, but never forgets a drain: a SIGUSR1 that arrived
        # in an earlier phase (or between two) still stops this one before it sends anything
        STOP.clear()
    # reachability per provider, once
    seen: set[str] = set()
    for e in model_entries:
        prov = provider_for(cfg, e)
        if prov.name in seen:
            continue
        seen.add(prov.name)
        if not prov.alive():
            raise SystemExit(f"provider {prov.name} ({prov.base_url}) not reachable — aborting")

    csvw = _Csv()
    summary: dict[str, dict] = {}
    try:
        for entry in model_entries:
            if STOP.is_set() or ABORT.is_set():
                log.warning("stop requested — skipping remaining models")
                break
            from . import quarantine
            if quarantine.queue_halted():
                log.error("queue halted — skipping remaining models")
                break
            label = entry["name"]
            if not (jobs_by_label and label in jobs_by_label):
                with _ACTIVE_LOCK:
                    ACTIVE[label] = (entry, provider_for(cfg, entry), cfg)
            try:
                # the adult page's per-model switch (adult_guard, lead 2026-11-15): a model with
                # adult_page off (always the Claude Code route) never plans an adult unit
                m_cases = _adult_guard.plan_cases(cfg, entry, cases, say=lambda s: (print(s), log.info(s)))
                rounds = plan_rounds(cfg, entry, m_cases, (jobs_by_label or {}).get(label),
                                     rung=rung, min_ctx=min_ctx)
                if len(rounds) > 1:
                    log.info("%s: %s", label, "; ".join(
                        f"round {r['name']} at ctx {r['ctx']}" for r in rounds))
                cont = (bench_run_ids or {}).get(label)
                rid = cont or str(uuid.uuid4())[:8]
                carry: dict = {"early_stop_cases": [c["id"] for c in m_cases]}
                if stage:
                    carry["stage"] = stage      # 4.5 routed run: "locator" | "module" on every row
                parts = []
                for i, rd in enumerate(rounds):
                    parts.append(_run_one_model(
                        cfg, entry, rd["cases"], csvw, smoke, only_jobs=rd["only_jobs"],
                        rung=rung, min_ctx=min_ctx, record_key=record_key,
                        reset=(i == 0 and not cont), bench_run_id=rid, perf=(i == 0 and not cont),
                        last=(i == len(rounds) - 1),
                        round_name=rd["name"] if len(rounds) > 1 else None, carry=carry,
                        skip_conformance=skip_conformance))
                summary[label] = _merge_round_summaries(parts)
            except RunStopped as e:
                _record_stop(cfg, entry, label, summary, jobs_by_label, record_key, rung,
                             reason=str(e))
                break
            except (ModelRunError, TransportError, WrongModelError,
                    lms.LmsError, studioforge.StudioForgeError) as e:
                if ABORT.is_set():
                    # the failure is the stop itself (a lease wait or a load
                    # poll abandoned on SIGTERM), not the model's
                    _record_stop(cfg, entry, label, summary, jobs_by_label, record_key, rung)
                    break
                log.error("model %s FAILED: %s — continuing with next model", label, e)
                summary[label] = {"failed": True, "error": str(e)[:2000]}
                # an early stop (R3) carries its machine-readable reason; the
                # partial rows stay but the run is never publishable
                es_extra = ({"early_stop": e.record, "publishable": False}
                            if isinstance(e, EarlyStopError) else None)
                if es_extra:
                    summary[label]["early_stop"] = e.record
                    try:
                        from . import quarantine
                        from .config import transcript_path
                        quarantine.hold_early_stop(label, e.record, str(transcript_path(label)))
                    except Exception:  # noqa: BLE001 — the stop stands even if the record cannot be written
                        log.error("quarantine record for %s failed", label)
                if jobs_by_label and label in jobs_by_label:
                    _write_recover_record(label, failed=True, error=str(e)[:500],
                                          key=record_key or ("escalated" if rung else "recovered"), rung=rung)
                else:
                    _write_meta(label, entry, provider_for(cfg, entry), failed=True,
                                error=str(e)[:2000], profile=cfg.get("_profile"),
                                revision=_revision(cfg), extra=es_extra)
            finally:
                with _ACTIVE_LOCK:
                    ACTIVE.pop(label, None)
    finally:
        csvw.close()
    return summary


#: Provider types that LOAD a model at a context (the context decides the
#: rig's slot count) — the only ones a round split helps.
_LOADING_TYPES = ("studioforge", "lmstudio")


def plan_rounds(cfg: dict, entry: dict, cases: list[dict], only_jobs: set | None = None,
                rung: int | None = None, min_ctx: int | None = None) -> list[dict]:
    """How a model's run is split into load rounds (EVAL-FABLE M7).

    A rig model loaded at 73,728 (what a code-bank row's 65,536 budget needs)
    gets only half the slots it gets at the 40,960 floor (STEP4: 4 instead of
    8). So the rows that need the wide window (bank rows, ladder rows with a
    budget/context) run in their OWN round at that context, every other row
    first in a round at the floor: round "A" (standard, narrow) then round
    "B" (wide), the model reloaded in between. Hosted APIs (nothing loaded),
    a round with only one kind of row, or a profile with
    ``run_plan.split_rig_rounds: false`` keep one round.

    Each round = {name, cases, only_jobs, ctx}. A board run passes the case
    SUBSET (never ``only_jobs``: that is recover mode — no perf, no meta); a
    jobs round (escalation, ladder) splits its job set."""
    def ctx_of(cs):
        return load_context(cfg, entry, rung=rung, min_ctx=min_ctx, cases=cs)

    pool = (cases if only_jobs is None
            else [c for c in cases if c["id"] in {j[1] for j in only_jobs}])
    one = [{"name": "all", "cases": cases, "only_jobs": only_jobs, "ctx": ctx_of(pool)}]
    try:
        ptype = provider_for(cfg, entry).type
    except Exception:  # noqa: BLE001 — an unknown provider fails later, loudly
        return one
    if (ptype not in _LOADING_TYPES or rung
            or not run_plan(cfg).get("split_rig_rounds", True)):
        return one
    wide = [c for c in pool if wide_context(cfg, [c])]
    narrow = [c for c in pool if not wide_context(cfg, [c])]
    if not wide or not [c for c in narrow if c.get("category") != "perf"]:
        return one
    ca, cb = ctx_of(narrow), ctx_of(wide)
    if ca == cb:
        return one
    if only_jobs is None:
        return [{"name": "A", "cases": narrow, "only_jobs": None, "ctx": ca},
                {"name": "B", "cases": wide, "only_jobs": None, "ctx": cb}]
    wid = {c["id"] for c in wide}
    return [{"name": "A", "cases": cases, "only_jobs": {j for j in only_jobs if j[1] not in wid},
             "ctx": ca},
            {"name": "B", "cases": cases, "only_jobs": {j for j in only_jobs if j[1] in wid},
             "ctx": cb}]


def _merge_round_summaries(parts: list[dict]) -> dict:
    out = dict(parts[-1])
    for k in ("rows", "skipped", "case_errors", "jobs"):
        out[k] = sum(int(p.get(k) or 0) for p in parts)
    out["load_s"] = round(sum(float(p.get("load_s") or 0) for p in parts), 1)
    out["warnings"] = [w for p in parts for w in (p.get("warnings") or [])]
    costs = [p.get("cost_usd") for p in parts if p.get("cost_usd") is not None]
    out["cost_usd"] = round(sum(costs), 4) if costs else None
    rms = [p.get("rig_minutes") for p in parts if p.get("rig_minutes") is not None]
    out["rig_minutes"] = round(sum(rms), 3) if rms else None
    if len(parts) > 1:
        out["rounds"] = [{"round": p.get("round"), "ctx": p.get("ctx"), "jobs": p.get("jobs"),
                          "workers": p.get("workers"), "plan": p.get("plan")} for p in parts]
    return out


CAP_STOP = "stopped: spend cap reached"


def cap_stop_reason(e) -> str:
    """The stop line for a spend-cap refusal: "stopped: spend cap reached ($7.39 of the $8.00 cap)"
    (lead 2026-11-14; it used to read "stopped by user"). A gate with no figures (an unpriced
    hosted model) keeps its own message after the prefix."""
    spent, cap = getattr(e, "spent", None), getattr(e, "cap", None)
    if spent is not None and cap is not None:
        return f"{CAP_STOP} (${spent:.2f} of the ${cap:.2f} {getattr(e, 'cap_name', 'cap')})"
    return f"{CAP_STOP} ({e})"


def _record_stop(cfg, entry, label, summary, jobs_by_label, record_key, rung,
                 reason: str | None = None) -> None:
    """Summary + meta for a model whose run was stopped: a hard stop marks the
    meta ``stopped`` (with a finished time), a cooperative one as before. A
    spend-cap stop (``reason`` from cap_stop_reason) says so instead of
    "stopped by user"."""
    hard = ABORT.is_set()
    if not hard and DRAIN.is_set() and not str(reason or "").startswith(CAP_STOP):
        # the cooperative drain: partial, not FAILED; the rest is generated by a later run
        cut = label in DRAIN_CUT
        error = DRAIN_NOTE + (f"; {CUT_NOTE}" if cut else "")
        summary[label] = {"failed": True, "error": error, "stopped": True, "drained": True,
                          **({"stream_cut": True} if cut else {})}
        if jobs_by_label and label in jobs_by_label:
            _write_recover_record(label, failed=True, error=f"stopped by user (drain){'; ' + CUT_NOTE if cut else ''}",
                                  key=record_key or ("escalated" if rung else "recovered"),
                                  rung=rung)
        else:
            _write_meta(label, entry, provider_for(cfg, entry), failed=False,
                        profile=cfg.get("_profile"), revision=_revision(cfg),
                        extra={"drain_stopped": _now(), "drain_note": error,
                               **({"stream_cut": True} if cut else {})})
        return
    if not hard and WINDOW_STOP.is_set():
        # bench-all's time window, not a failure: the rows written stay, the
        # rest is generated by the next run (the board shows the model as
        # partial meanwhile, never FAILED)
        error = "stopped at the time window edge — the rest next run"
        summary[label] = {"failed": True, "error": error, "stopped": True, "window": True}
        if jobs_by_label and label in jobs_by_label:
            _write_recover_record(label, failed=True, error="stopped by user (time window)",
                                  key=record_key or ("escalated" if rung else "recovered"),
                                  rung=rung)
        else:
            _write_meta(label, entry, provider_for(cfg, entry), failed=False,
                        profile=cfg.get("_profile"), revision=_revision(cfg),
                        extra={"window_stopped": _now()})
        return
    cap = not hard and str(reason or "").startswith(CAP_STOP)
    error = "stopped (SIGTERM)" if hard else (reason if cap else "stopped by user")
    summary[label] = {"failed": True, "error": error, "stopped": True, **({"spend_cap": True} if cap else {})}
    if jobs_by_label and label in jobs_by_label:
        _write_recover_record(label, failed=True, error=error, stopped=hard or None,
                              key=record_key or ("escalated" if rung else "recovered"), rung=rung)
    else:
        _write_meta(label, entry, provider_for(cfg, entry), failed=True, error=error,
                    profile=cfg.get("_profile"), revision=_revision(cfg), stopped=hard)


def mark_active_stopped() -> list[str]:
    """Mark every model run still in flight ``stopped`` (the stop watchdog's
    last act before it ends the process). Returns the labels marked."""
    with _ACTIVE_LOCK:
        items = list(ACTIVE.items())
    out = []
    for label, (entry, provider, cfg) in items:
        try:
            _write_meta(label, entry, provider, failed=True, error="stopped (SIGTERM)",
                        profile=cfg.get("_profile"), revision=_revision(cfg), stopped=True)
            out.append(label)
        except Exception as e:  # noqa: BLE001 — best effort on the way out
            log.warning("could not mark %s stopped: %s", label, e)
    return out


def _vendor_stamp_path() -> Path:
    """Sidecar with the per-run model-version + date stamp the runner stamps
    into every meta_<label>.json it writes. The orchestrator writes this ONCE
    before launching the bench (probe first → stamp → bench); the runner
    reads it on every _write_meta() so back-to-back profiles (coding → chat)
    land on the same stamp without re-probing. See SKILL.md "Model version +
    date annotation" (maintainer, 2026-09-09)."""
    return results_dir() / "_stamp.json"


def _read_vendor_stamp(model_id: str | None = None) -> dict:
    """The sidecar's three fields, ONLY when the sidecar names the model it
    probed (``model_id``) and that is the model being written. The sidecar is
    one global file: before 2026-09-23 it carried no model id, so a single
    DeepSeek probe from 2026-09-09 was copied into every model's meta and the
    whole board read "vdeepseek-v4-flash on 2026-09-09". A stamp without a
    model id, or for another model, is ignored. Missing or unparseable
    sidecar → empty stamp."""
    p = _vendor_stamp_path()
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(d, dict) or not d.get("model_id") or d.get("model_id") != model_id:
        return {}
    out = {}
    for k in ("model_version_resolved", "test_date_utc", "vendor_probe_source"):
        v = d.get(k)
        if v:
            out[k] = v
    return out


def _write_meta(label: str, entry: dict, provider: Provider, *, load_s: float | None = None,
                bench_run_id: str | None = None, failed: bool = False,
                error: str | None = None, finished: bool = False,
                profile: str | None = None, plan: dict | None = None,
                ctx_len: int | None = None, warnings: list[str] | None = None,
                revision: str | None = None, reset: bool = False,
                started: str | None = None, regime: dict | None = None,
                stopped: bool = False, extra: dict | None = None) -> None:
    """``reset=True`` (the first write of a new run) starts from an empty
    meta, so a previous run's ``finished``/``error``/``warnings`` never leak
    onto this one."""
    path = results_dir() / f"meta_{label}.json"
    meta = {}
    if path.exists() and not reset:
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    prev_model_id = meta.get("model_id")
    meta.update({
        "model_label": label, "model_id": entry["model_id"],
        "device": provider.name, "provider": provider.name,
        "provider_type": provider.type,
        "updated": _now(), "failed": failed,
    })
    # Stamp the vendor version + date on every write so back-to-back profiles
    # (coding → chat, 2026-09-08 split) and a mid-run failure both carry the
    # same probe snapshot.
    #
    # If the orchestrator wrote `results/_stamp.json`, it is the source of
    # truth for THIS run — overwrite any pre-existing stamp on the meta. If
    # the sidecar is absent (older orchestrator path; a hand-backfilled
    # meta), preserve whatever was already on the file: an old stamp is a
    # better answer than no stamp at all.
    stamp = _read_vendor_stamp(entry["model_id"])
    if stamp:
        meta.update(stamp)
    elif prev_model_id and prev_model_id != entry["model_id"]:
        # a meta left over from a different model id under this label must
        # not keep that model's version stamp
        for k in ("model_version_resolved", "test_date_utc", "vendor_probe_source"):
            meta.pop(k, None)
    if entry.get("price"):
        meta["price"] = entry["price"]
    if provider.type == "claude_code":
        # sampling is the Claude Code harness's own and cannot be set: rows are
        # not comparable to API rows (docs/CONFIG.md, claude_code provider)
        from .claudecode import SAMPLING_NOTE
        meta["sampling"] = "harness-default"
        meta["sampling_note"] = SAMPLING_NOTE
        meta["cost_basis"] = "subscription"
        from .claudecode import effort_for
        meta["cc_effort"] = effort_for(entry)    # the explicit --effort every row of the run passed
    if profile:
        meta["profile"] = profile
        # Split profiles (`coding` + `chat`, 2026-09-08) land on the SAME
        # label; keep every profile that contributed rows so the report can
        # say "profile coding+chat" instead of just the last one.
        meta["profiles"] = sorted(set(meta.get("profiles") or []) | {profile})
    if load_s is not None:
        meta["load_s"] = round(load_s, 1)
    if plan:
        meta["plan"] = plan  # the placement the numbers were measured under
    if profile:
        try:
            from . import profiles as _pr
            _p = _pr.load_profile(profile)
            rs = (_p.get("run_settings") or {})
            if _p.get("composition"):
                meta["composition"] = _p["composition"]   # which Main composition the rows answer
        except Exception:  # noqa: BLE001 - an unknown profile has no run settings
            rs = {}
        if rs:
            meta["run_settings"] = dict(rs)  # e.g. kv_cache_type: the same for every model
    if ctx_len:
        meta["context_length"] = int(ctx_len)
    if bench_run_id:
        meta["bench_run_id"] = bench_run_id
    if reset:
        meta["started"] = started or meta["updated"]
    if revision:
        meta["bench_revision"] = revision
    if regime:
        meta["regime"] = regime
    if warnings:
        meta["warnings"] = list(warnings)
    if error:
        meta["error"] = error
    if extra:
        meta.update(extra)
    if failed:
        meta.pop("finished", None)
    if finished:
        meta["finished"] = _now()
        meta.pop("stopped", None)
    if stopped:
        # a hard stop (SIGTERM): the run ended here, on purpose, unfinished —
        # ``failed`` keeps the board from treating it as complete
        meta["stopped"] = True
        meta["finished"] = meta["stopped_at"] = _now()
    results_dir().mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def run_plan(cfg: dict) -> dict:
    """The profile's ``run_plan:`` block (profiles/bench.yaml, "Run plan
    (step 5 runner)"), copied into cfg.defaults by apply_profile."""
    return dict((cfg.get("defaults") or {}).get("run_plan") or {})


def fixed_budget(cfg: dict, case: dict) -> int | None:
    """The one output budget a row is sent whatever the model, when the case
    has one: the code bank's ``bank_budget`` (bank rows), else a ladder set's
    ``ladder_budget`` (cases/ladder/.../set-<n>.json "budget": harder level,
    more room — config, not code). None = the board regime's budget."""
    if case.get("category") in BANK_CATEGORIES:
        b = int(cfg["defaults"].get("bank_budget") or 0)
        return b or None
    if case.get("chain_spec"):
        # 4.5 chains: the load floor must hold the largest rung ceiling (chains.py) plus, for
        # a multi-turn item, its ``history_budget`` (the room the accumulated history may
        # take: earlier turns' prompts, replies and replayed thinking)
        cs = [int(r["ceiling"]) + int(r.get("history_budget") or 0)
              for r in case["chain_spec"]["rungs"] if r.get("ceiling")]
        return max(cs) if cs else None
    if case.get("ladder_budget"):
        return int(case["ladder_budget"])
    if case.get("budget"):
        # 4.0 lite: a board case's own budget (cases/coding.json CL "budget":
        # the function set is graded on code, not on fitting the 24,576 cap)
        return int(case["budget"])
    return None


def wide_context(cfg: dict, cases=None) -> int | None:
    """The load floor a round needs so every fixed-budget row (bank rows,
    ladder rows with a budget) gets its WHOLE budget instead of one clamped
    to a small window (budget + ESCALATION_CTX_PAD: 73,728 for 65,536), and
    at least a ladder set's own ``context``. None = no such row."""
    best = None
    for c in cases or []:
        b = fixed_budget(cfg, c)
        need = (b + ESCALATION_CTX_PAD) if b else None
        if c.get("ladder") and c.get("min_context"):
            need = max(need or 0, int(c["min_context"]))
        if need:
            best = max(best or 0, need)
    return best


def bank_context(cfg: dict, cases=None) -> int | None:
    """The load floor a round with code-bank rows needs so every bank row gets
    the FULL ``bank_budget`` (65,536) instead of a budget clamped to a small
    rig window: bank_budget + ESCALATION_CTX_PAD (73,728, the T3 context).
    None when the round has no bank rows or the bank budget is off."""
    b = int(cfg["defaults"].get("bank_budget") or 0)
    if not b or not cases:
        return None
    if not any(c.get("category") in BANK_CATEGORIES for c in cases):
        return None
    return b + ESCALATION_CTX_PAD


def load_context(cfg: dict, entry: dict, rung: int | None = None,
                 min_ctx: int | None = None, cases=None) -> int | None:
    """The context a model is loaded at: its registry context, raised (never
    lowered) to the profile's ``context_floor`` (3.9: rpdeep needs 40960), a
    tier round's context (``tiers.t3.context``) and, when the round has
    code-bank rows, the bank's load floor (``bank_context``: 73,728, so bank
    rows get the full 65,536 budget, not one clamped to the window); an
    escalation rung needs room for a rung-sized answer."""
    explicit = entry.get("context") or entry.get("load_ctx")
    ctx_len = explicit or entry.get("context_length") or cfg["defaults"].get("context_length")
    thinker = cfg["defaults"].get("thinker_context")
    if thinker and not explicit and entry.get("thinking") is True:
        # D14: a registry-declared thinker loads at least this wide (profile
        # ``thinker_context``, 4.5: 73,728); a per-model ``context`` wins
        ctx_len = max(int(ctx_len or 0), int(thinker))
    for floor in (cfg["defaults"].get("context_floor"), min_ctx, bank_context(cfg, cases),
                  wide_context(cfg, cases)):
        if floor and ctx_len:
            ctx_len = max(int(ctx_len), int(floor))
    if rung:
        # the answer must fit: prompt + a rung-sized completion
        ctx_len = max(int(ctx_len or 0), int(rung) + ESCALATION_CTX_PAD)
    return ctx_len


class _CopyLost(Exception):
    """The subject copy a job ran on lost its model (subject_copies): the job moves."""


def _run_one_model(*args, **kw) -> dict:
    """One load round of one model (:func:`_run_one_model_body`); the subject copies
    it brought up (subject_copies) are torn down on every exit path."""
    placed: list = []
    try:
        return _run_one_model_body(*args, _placed=placed, **kw)
    finally:
        for pl in placed:
            pl.tear_down()


def _run_one_model_body(cfg, entry, cases, csvw: _Csv, smoke,
                   only_jobs: set | None = None, rung: int | None = None,
                   min_ctx: int | None = None, record_key: str | None = None,
                   reset: bool = True, bench_run_id: str | None = None, perf: bool = True,
                   last: bool = True, round_name: str | None = None,
                   carry: dict | None = None,
                   skip_conformance: bool = False, _placed: list | None = None) -> dict:
    """One load round of one model (run_models splits a run into rounds,
    :func:`plan_rounds`). ``reset``: the first round of a board run starts a
    fresh meta; ``perf``: the perf probes run in this round; ``last``: this
    round marks the run finished; ``bench_run_id``: shared by every round of
    one run; ``carry``: what an earlier round learnt (thinking)."""
    t_round = datetime.now(timezone.utc)
    label = entry["name"]
    model_id = entry["model_id"]
    # the bank load floor applies only when this round really runs bank rows
    # (a recover/tier round restricted to chat jobs keeps its smaller window)
    ctx_cases = (cases if only_jobs is None
                 else [c for c in cases if c["id"] in {j[1] for j in only_jobs}])
    ctx_len = load_context(cfg, entry, rung=rung, min_ctx=min_ctx, cases=ctx_cases)
    provider = provider_for(cfg, entry)
    bench_run_id = bench_run_id or str(uuid.uuid4())[:8]
    carry = carry if carry is not None else {}
    # R3 early stop: judged on the run's FIRST answers, so one counter spans
    # every load round of a board run (carried); a recover / escalation round
    # re-runs chosen jobs and is not judged
    es = None
    if only_jobs is None and not rung:
        es = carry.get("early_stop")
        if es is None:
            # carry["early_stop_cases"]: the whole run's case ids (run_models;
            # a round sees only its own subset)
            es = carry["early_stop"] = earlystop.EarlyStop(
                cfg, label=label, skip=earlystop.targeted_reason(
                    cfg, carry.get("early_stop_cases") or [c["id"] for c in cases]))
    ctx = _Ctx(cfg, entry, provider, ctx_len)
    if ctx.thinking is None and carry.get("thinking") is not None:
        ctx.thinking = carry["thinking"]
        ctx.thinking_source = carry.get("thinking_source") or "earlier round"
    ctx.rung = int(rung) if rung else None
    if rung and run_plan(cfg).get("escalation_max_wall_s"):
        ctx.max_wall_s = float(run_plan(cfg)["escalation_max_wall_s"])
    if rung:
        # every case of the round gets the rung (a thinking model's budget is
        # the cap; floor = cap so a short-budget case is not cut below it)
        ctx.think_cap = ctx.think_floor = int(rung)
    recover_mode = only_jobs is not None
    # a model-local abort (sanity / speed floor / transport storm) must not
    # touch the global STOP, or every later model in the batch is skipped
    abort = threading.Event()

    t_start = _now()
    log.info("=== %s (%s) via %s [%s], ctx=%s%s ===", label, model_id,
             provider.name, provider.type, ctx_len,
             f", round {round_name}" if round_name else "")
    if not provider.is_available(model_id):
        raise ModelRunError(f"{model_id} is not served by provider {provider.name}")
    # the rig refuses a load past the model's trained window (HTTP 400), so a
    # small model would fail outright at the bench context: load it at its
    # trained window instead — the rows that cannot fit are skipped (n/a)
    # below, exactly like a rig that serves a narrower window than asked
    trained = (provider.trained_context(model_id)
               if hasattr(provider, "trained_context") else None)
    if trained and ctx_len and int(ctx_len) > trained:
        if rung and trained < int(rung):
            raise ModelRunError(f"escalation to {rung} tokens needs ctx >= {rung}; {label} is "
                                f"trained to {trained} — rung skipped")
        log.warning("%s is trained to %d tokens, below the bench context %s — loading at %d; "
                    "cases that need more are skipped (n/a), not failed",
                    label, trained, ctx_len, trained)
        ctx_len = trained
        ctx.ctx_len = trained
    wanted_ctx = ctx_len        # what the registry asks for (before a narrower window is accepted)
    copies = (_subject_copies.declared(cfg, entry)
              if getattr(provider, "type", None) == "studioforge" else [])
    if copies:
        # declared copies (subject_copies): each leased and loaded EXPLICITLY on its cards
        # with the run's KV type and slot ladder, verified, parity-checked; never switch_model
        placement = _subject_copies.Placement(provider, copies, ctx_len)
        if _placed is not None:
            _placed.append(placement)
        try:
            placement.bring_up()
        except _subject_copies.CopyError as e:
            raise ModelRunError(f"{label}: {e}") from e
        ctx.placement = placement
        # from here on the run talks to the copies only: a chat to the base id goes to a
        # copy and a load/switch of the base id raises (02:10 incident rule)
        provider = ctx.provider = _subject_copies.PlacedProvider(provider, placement)
        load_s = placement.load_s
        if placement.ctx and ctx_len and placement.ctx > int(ctx_len):
            ctx_len = wanted_ctx = placement.ctx
            ctx.ctx_len = ctx_len
    else:
        load_s = provider.switch_model(model_id, ctx_len)
    live_ctx = provider.live_context(model_id)
    if live_ctx and ctx_len and live_ctx < int(ctx_len):
        log.warning("%s is serving ctx=%d, below the registry context %s — long-context "
                    "cases beyond %d are skipped (n/a), not failed", label, live_ctx, ctx_len,
                    live_ctx)
        ctx_len = live_ctx
        ctx.ctx_len = live_ctx
        if rung and live_ctx < int(rung):
            raise ModelRunError(f"escalation to {rung} tokens needs ctx >= {rung}; the rig "
                                f"serves {label} at {live_ctx} — rung skipped")
    plan = provider.loaded_plan_for(model_id) if hasattr(provider, "loaded_plan_for") else {}
    if ctx.placement is not None:
        plan = {**(plan or {}), "placement": _placement_record(cfg, ctx.placement)}
    if not recover_mode and reset:
        _write_meta(label, entry, provider, load_s=load_s, bench_run_id=bench_run_id,
                    profile=cfg.get("_profile"), plan=plan or None, ctx_len=ctx_len,
                    revision=_revision(cfg), reset=True, started=t_start)
    ctx.adopt_rig_reasoning()
    ctx.detect_thinking()

    state = {"rows": 0, "sanity_total": 0, "sanity_empty": 0,
             "speed_samples": [], "speed_gated": False, "skipped": 0,
             "cost": 0.0, "consecutive_errors": 0, "case_errors": 0,
             # "first N replies all blocked" only means a refusing provider on a
             # label's very first generation; a targeted re-run of blocked cases
             # (the rows already exist) is legitimate
             "block_guard": (not recover_mode and not rung
                             and not (transcript_path(label).exists()
                                      and transcript_path(label).stat().st_size > 0))}
    chain_rows: dict[str, list[dict]] = {}      # chain attempt rows, by instance id (chains.Driver)
    min_tps = float(cfg["defaults"].get("min_tok_per_s", 0) or 0)
    revision = _revision(cfg)
    judge_fp = _judge_fp(cfg)
    # the request protocol every row of this round is measured under (the
    # conformance fingerprint): a harness change moves it, so a stale row can
    # be told from an old one (R3)
    protocol = protocol_stamp(cfg, entry, provider, ctx)

    # The conformance gate (Part 3): a model is benched only under a request
    # protocol that has been PROVEN for it.  An API model is gated before the
    # run (cli._conformance_gate); a rig model can only be checked while it
    # is loaded, so it is gated HERE, right after the load.  A FAIL means the
    # model is NOT run: the reason names the check, and the board and the
    # run summary say so.
    if provider.type in ("studioforge", "lmstudio"):
        from . import conformance as _conformance
        gate = _conformance.gate(cfg, entry, provider=provider, ctx=ctx,
                                 skip=skip_conformance, loaded=True)
        if not gate.ok:
            raise ModelRunError(gate.reason)
        _write_meta(label, entry, provider, profile=cfg.get("_profile"),
                    revision=_revision(cfg),
                    extra={"conformance": {"verdict": gate.verdict,
                                           "ts": (gate.record or {}).get("ts"),
                                           "protocol": protocol}})

    def base_row_for(case, repeat, seed, temperature, top_p, max_tokens, rid=None):
        extra = {}
        if case["category"] in _config.PRIVATE_CATEGORIES:
            # a private case is outside bench_revision (version.cases_hash);
            # this is the stamp that says which private content it answered
            extra["private_revision"] = private_category_hash(case["category"])
        else:
            # 3.10 (version.row_current): the per-case content stamp of every
            # public row — editing one case re-generates only that case's rows
            from .version import case_hash
            cr = case_hash(case["id"])
            if cr:
                extra["case_revision"] = cr
        if case["category"] in BANK_CATEGORIES:
            from .version import bank_case_hash
            extra["bank_revision"] = bank_case_hash(case["category"], case["id"])
        if case.get("chain"):
            # 4.5 chains (chains.py): the instance's chain block + its own content stamp
            from .chains import revision as _chain_revision
            extra["chain"] = dict(case["chain"])
            extra["chain_revision"] = _chain_revision(case["chain"])
            if case.get("judge_elements"):
                # a judged flat instance is not in load_cases: the judge reads its elements and
                # case fields off the row (judge.row_elements_all / case_field are row-first)
                from .judge import _CASE_FIELD_KEYS
                extra["judge_elements"] = copy.deepcopy(case["judge_elements"])
                extra.update({k: copy.deepcopy(case[k]) for k in _CASE_FIELD_KEYS if k in case})
        if case.get("route"):
            # 4.5 bank (routing.py): the route this module unit was given, by locator score
            extra["route"] = copy.deepcopy(case["route"])
        if case.get("pack_gate"):
            # a routed part's pack: the gate it opened on (target, share, threshold, opened, reason)
            extra["pack_gate"] = copy.deepcopy(case["pack_gate"])
        if case.get("ladder_pack"):
            # a question written inside a ladder pack: outside bench_revision,
            # its own content stamp (version.ladder_case_hash)
            from .version import ladder_case_hash
            extra["ladder_revision"] = ladder_case_hash(case["id"])
        if rung:
            extra["budget_rung"] = int(rung)
        if case.get("tier"):
            # suite 3.9: a tier-2/3 row (unlocked per score, tiers.py)
            extra["tier"] = int(case["tier"])
        return {**extra, **ctx.regime_stamp(),
            "bench_run_id": rid or bench_run_id,
            "bench_revision": revision, "judge_fingerprint": judge_fp,
            "profile": cfg.get("_profile"),
            "protocol": protocol,
            "ts": _now(),
            "model_label": label, "model_id": model_id, "device": provider.name,
            "provider": provider.name,
            "category": case["category"], "case_id": case["id"], "repeat": repeat,
            "difficulty": case.get("difficulty", "medium"),
            "seed": seed, "temperature": temperature, "top_p": top_p,
            "max_tokens_sent": max_tokens,
            "load_ctx": ctx_len,
            **({"round": round_name} if round_name else {}),
        }

    perf_seen: list[dict] = []     # this round's perf probe rows (the run-start sanity rule)

    def persist(rows):
        with ctx.lock:
            # a hard stop drops in-flight jobs: one that still returns after
            # run_models gave up on it must not write a row behind the
            # stopped meta (test_request_stop_returns_run_models_within_seconds)
            if ABORT.is_set():
                if rows:
                    log.info("[%s] stopped — dropping %d late row(s) of %s",
                             label, len(rows), rows[0].get("case_id"))
                return
            held = _subject_copies.current()
            for row in rows:
                if held is not None:
                    row["subject_copy"] = held.audit()     # audit only: model_id stays canonical
                m = row.get("metrics") or {}
                c = cost_usd(entry,
                             (m.get("prompt_tokens") or 0) + (m.get("billed_extra_prompt_tokens") or 0)
                             if m.get("prompt_tokens") is not None or m.get("billed_extra_prompt_tokens") else None,
                             (m.get("completion_tokens") or 0) + (m.get("billed_extra_completion_tokens") or 0)
                             if m.get("completion_tokens") is not None or m.get("billed_extra_completion_tokens") else None,
                             (m.get("cache_read_tokens") or 0) + (m.get("billed_extra_cache_read_tokens") or 0),
                             (m.get("cache_write_tokens") or 0) + (m.get("billed_extra_cache_write_tokens") or 0),
                             native_cache=m.get("cache_write_tokens") is not None)
                if provider.type == "claude_code":
                    # subscription, not metered: cost 0, and say so (after cache45's pricing,
                    # so the metered providers keep their cache-aware cost)
                    c = 0.0
                    row["cost_basis"] = "subscription"
                if c is not None:
                    row["cost_usd"] = round(c, 6)
                    state["cost"] += c
                rm = rig_minutes(provider, plan, m)
                if rm is not None:
                    # R23 (2026-11-13): a rig model has no dollars; its cost is rig time =
                    # wall-clock x the share of the rig the row held (cards / RIG_CARDS / slots)
                    row["rig_minutes"] = rm
                    row.setdefault("cost_basis", "rig")
                    state["rig_min"] = state.get("rig_min", 0.0) + rm
                stamp_degenerate(row)
                if carry.get("stage"):
                    row["stage"] = carry["stage"]
                if row.get("category") == "perf":
                    perf_seen.append(row)
                append_transcript(label, row)
                if row.get("chain"):
                    chain_rows.setdefault(row["case_id"], []).append(row)
                csvw.write({**row, "run_id": row["run_id"],
                            "grade": row.get("grade"),
                            "grade_detail": row.get("grade_detail"),
                            **(row.get("metrics") or {})})
                state["rows"] += 1
            _sanity_check(state, rows, label)
            if es is not None:
                had_error = es.error
                trip = es.observe(rows)
                if es.error and not had_error:
                    log.error("[%s] %s — the run is not publishable", label, es.error)
                if trip:
                    trip = {**trip, "bench_run_id": bench_run_id}
                    log.error("[%s] %s — stopping this model, rows kept", label,
                              earlystop.describe(trip))
                    raise EarlyStopError(trip)
            # viability floor: collect real gen tok/s samples; once we have a
            # few, abort if the median is below the floor (too slow to be usable)
            if min_tps > 0 and not state["speed_gated"]:
                for row in rows:
                    tps = (row.get("metrics") or {}).get("tok_per_s")
                    if tps:
                        state["speed_samples"].append(tps)
                if len(state["speed_samples"]) >= 2:
                    ss = sorted(state["speed_samples"])
                    med = ss[len(ss) // 2]
                    state["speed_gated"] = True
                    if med < min_tps:
                        raise ModelRunError(
                            f"too slow: {med:.2f} tok/s < {min_tps} floor "
                            f"(n={len(ss)}) — model not viable, aborting")

    def run_case_repeat(case, repeat, rid=None):
        pl = ctx.placement
        if pl is None:
            return _run_case_repeat(case, repeat, rid)
        tried: set = set()
        while True:
            try:
                with pl.pool.slot(exclude=tried, abort=abort) as held:
                    try:
                        return _run_case_repeat(case, repeat, rid)
                    except _CopyLost as e:
                        tried.add(held.name)
                        others = [c for c in pl.pool.live() if c is not held]
                        if others:
                            pl.pool.mark_dead(held, str(e))
                        elif not pl.reload(held):
                            raise ModelRunError(f"{label}: the last copy of the subject is "
                                                f"gone ({held.died})") from e
                        log.warning("[%s] %s r%d: copy %s lost (%s) — re-running the job on "
                                    "a live copy", label, case["id"], repeat, held.name, e)
            except _subject_copies.SlotAborted:
                return
            except _subject_copies.NoCopyLeft as e:
                raise ModelRunError(f"{label}: {e}") from e

    def _held_lost() -> str | None:
        """With declared copies: why the copy this job holds is no longer serving."""
        pl, held = ctx.placement, _subject_copies.current()
        if pl is None or held is None:
            return None
        return None if pl.healthy(held) else f"{held.serve_id} not loaded as declared"

    def _run_case_repeat(case, repeat, rid=None):
        _adult_guard.assert_sendable(cfg, entry, case)
        if STOP.is_set():
            raise RunStopped()
        check_abort()
        if abort.is_set():
            return
        # every priced call this job makes is journalled under it (spend.py)
        spend.set_context(label=label, case_id=case.get("id"), repeat=repeat)
        temperature, top_p = ctx.sampling(case)
        max_tokens = case["max_tokens"]
        # a sampled row (any temperature but 0) gets a seed per repeat, so
        # repeats are independent draws
        seed = writer_seed(case, repeat, temperature)
        base_row = base_row_for(case, repeat, seed, temperature, top_p, max_tokens, rid)

        need_ctx = int(case.get("min_context") or 0)
        # a code-bank row is sent its WHOLE fixed budget (bank_budget), so it
        # needs the budget plus the escalation pad of context (the load floor,
        # bank_context): on a narrower window the budget would be clamped down
        # and the row could still "pass" — skip it (n/a) like every other
        # over-budget row instead
        if case.get("category") in BANK_CATEGORIES:
            b = fixed_budget(cfg, case)
            if b:
                need_ctx = max(need_ctx, int(b) + ESCALATION_CTX_PAD)
        if need_ctx and ctx_len and need_ctx > int(ctx_len):
            row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
                   "prompt": case.get("prompt", "")[:200], "response": "",
                   "reasoning": "", "tool_calls": [], "finish_reason": None,
                   "truncated": False, "skipped": True,
                   "grade": "skipped",
                   "grade_detail": f"needs {need_ctx} ctx > model {ctx_len}",
                   "metrics": {}}
            persist([row])
            with ctx.lock:
                state["skipped"] += 1
            return

        lost = _held_lost()
        if lost:
            raise _CopyLost(lost)
        if ctx.placement is None and not provider.is_loaded(model_id):
            log.warning("%s evicted — reloading", model_id)
            check_abort()
            # not under ctx.lock: a reload waits up to wait_ready's 900 s and
            # every other worker's persist() needs ctx.lock (M10)
            with ctx.reload_lock:
                if not provider.is_loaded(model_id):
                    provider.switch_model(model_id, ctx_len)

        def _generate():
            if case.get("needs_chain"):
                # FC1 PATTERN: turn 2 carries the model's own turn-1 reply; the standard runner has no chain yet
                raise GenerationRejected("needs the t1->t2 chain runner (T7 second half): recorded as a missing row")
            if case.get("console"):
                return [_run_console_loop(case, base_row, ctx, max_tokens, temperature, seed, top_p=top_p)]
            if case.get("env"):
                return [_run_env_loop(case, base_row, ctx, max_tokens, temperature, seed, top_p=top_p)]
            if case.get("tool_script"):
                return [_run_tool_loop(case, base_row, ctx, max_tokens, temperature, seed, top_p=top_p)]
            if case.get("turns"):
                return _run_multiturn(case, base_row, ctx, max_tokens, temperature, top_p, seed)
            return [_run_single(case, base_row, ctx, max_tokens, temperature, top_p, seed)]

        def _widen_possible() -> bool:
            """A retry can change something only if the server serves a
            narrower window than the model's context (a reload fixes it)."""
            live = provider.live_context(model_id) if hasattr(provider, "live_context") else None
            return bool(live and wanted_ctx and int(live) < int(wanted_ctx))

        def _generate_checked():
            """4.5: a row cut by the context window (finish=length with the
            window full, or the server's own context error) is infrastructure:
            retried once at the model's context, then recorded as an errored
            (missing) row. ``ctx_short_retry: 0`` restores the old behaviour."""
            retries = int(cfg["defaults"].get("ctx_short_retry", 0) or 0)
            if case.get("category") in BANK_CATEGORIES:
                retries = 0           # bank rows are skipped up front when they cannot fit
                #                        (need_ctx above); a rejection there stays the model's
            short = False
            for attempt in range(retries + 1):
                try:
                    rows = _generate()
                    short = any(row_ctx_short(r, ctx.ctx_len) for r in rows)
                except RequestRejected as e:
                    if not (retries and e.status in _CASE_LOCAL_REJECT_STATUSES
                            and is_ctx_short_error(str(e))):
                        raise
                    rows, short = None, True
                if not short:
                    return rows
                if attempt < retries and _widen_possible():
                    log.warning("[%s] %s r%d: cut by the context window (ctx %s) — "
                                "reloading at the model's context and retrying once",
                                label, case["id"], repeat, ctx.ctx_len)
                    check_abort()
                    with ctx.reload_lock:
                        provider.switch_model(model_id, wanted_ctx)
                    now = provider.live_context(model_id) if hasattr(provider, "live_context") else None
                    ctx.ctx_len = int(now or wanted_ctx)
                    base_row["load_ctx"] = ctx.ctx_len     # the retry is logged at the value it used
                    continue
                break
            if rows and (case.get("chain") or (trained and wanted_ctx
                                               and int(wanted_ctx) >= int(trained))):
                # (a chain row is sent the whole window: its end IS the model's maximum, R23)
                # the model's OWN trained window ran out: verbosity is the
                # model's failure, not infrastructure -> graded as ran-out
                log.warning("[%s] %s r%d: cut at the model's trained context (%s) - "
                            "graded as ran-out", label, case["id"], repeat, trained)
                for r_ in rows:
                    if row_ctx_short(r_, ctx.ctx_len):
                        r_["ctx_short_model_limit"] = True
                return rows
            log.error("[%s] %s r%d: still cut by the context window (ctx %s) — recorded "
                      "as an errored row, not scored", label, case["id"], repeat, ctx.ctx_len)
            with ctx.lock:
                state["case_errors"] += 1
            return [_error_row(base_row, case,
                               f"ctx_short: the context window ({ctx.ctx_len}) cut the "
                               f"output; not scored", kind="ctx_short")]

        try:
            rows = _generate_checked() if int(cfg["defaults"].get("ctx_short_retry", 0) or 0) \
                else _generate()
        except GenerationRejected as e:
            # the MODEL's output could not be delivered (malformed tool-call
            # args, a chat template llama-server cannot build a parser for) —
            # that is this case failing, not the run
            log.warning("[%s] %s r%d: server rejected the model's generation: %s",
                        label, case["id"], repeat, str(e)[:200])
            with ctx.lock:
                state["case_errors"] += 1
            persist([_error_row(base_row, case, f"server rejected the model's output: {e}",
                                kind=_generation_reject_kind(str(e)))])
            return
        except (TransportError, WrongModelError) as e:
            if isinstance(e, Aborted) or ABORT.is_set():
                # stopping: no row — the job stays pending for the next run
                raise RunStopped("stopped (SIGTERM)") from e
            lost = _held_lost()
            if lost:
                raise _CopyLost(f"{lost} ({str(e)[:120]})") from e
            if isinstance(e, spend.SpendCapExceeded):
                # the spend cap, not the clock: the stop says so (lead 2026-11-14); no row, the
                # job stays pending like any other stop
                raise RunStopped(cap_stop_reason(e)) from e
            if isinstance(e, WindowClosed) and stopflag.CUT.is_set():
                # the drain ceiling cut this stream: no row, the job stays pending; the
                # model must be reloaded before the next run (the run's notes say so)
                with _DRAIN_LOCK:
                    DRAIN_CUT.add(label)
                raise RunStopped("drain ceiling: stream cut") from e
            if isinstance(e, WindowClosed):
                # the run's time window closed mid-request (bench-all
                # --until): no row, the job is generated by the next run
                raise RunStopped("time window closed") from e
            if isinstance(e, RowTimeout) and ctx.max_wall_s:
                # a rung round's wall cap (S10): the model did not finish in
                # time on this rung — it ran out, it did not error. Not counted
                # as a transport failure; not re-escalated (finish is not
                # "length", a bigger rung would only take longer).
                log.warning("[%s] %s r%d ran past the rung's %.0fs wall cap", label, case["id"],
                            repeat, ctx.max_wall_s)
                row = _error_row(base_row, case, f"ran past the {ctx.max_wall_s:.0f}s wall-clock "
                                 f"cap of the {rung}-token rung", kind="generation")
                row.update(finish_reason="wall_timeout", truncated=True, error_kind="wall")
                row.setdefault("grade", "fail")
                persist([row])
                return
            if isinstance(e, RequestRejected) and e.status in _CASE_LOCAL_REJECT_STATUSES:
                # a request the server refuses as malformed (HTTP 400/422 —
                # e.g. DeepSeek's thinking mode demanding reasoning_content
                # back) is this case erroring, not the server going away
                log.error("[%s] %s r%d request rejected: %s", label, case["id"], repeat,
                          str(e)[:200])
                with ctx.lock:
                    state["case_errors"] += 1
                # code bank: a request the model's window cannot take (or any
                # other case-local rejection) is the model FAILING the task at
                # this budget, never an excluded row (review 2026-10-26)
                kind = _rejection_kind(case, e)
                persist([_error_row(base_row, case, f"request rejected: {e}", kind=kind)])
                return
            with ctx.lock:
                state["consecutive_errors"] += 1
                state["case_errors"] += 1
                n = state["consecutive_errors"]
            log.error("[%s] %s r%d transport failure (%d in a row): %s",
                      label, case["id"], repeat, n, str(e)[:200])
            # recorded as an ERRORED row (grade "error"): excluded from the
            # score and listed in failures.md — the server failed, not the model
            persist([_error_row(base_row, case, f"transport failure: {e}", kind="transport")])
            if n >= MAX_CONSECUTIVE_TRANSPORT_ERRORS:
                abort.set()
                raise ModelRunError(
                    f"{n} consecutive transport failures for {label} — server "
                    f"unreachable/unstable, aborting this model (last: {str(e)[:160]})")
            return
        with ctx.lock:
            state["consecutive_errors"] = 0
        if case.get("chain"):
            # the attempt's in-run verdict (chains.py): the chain's next step depends on it
            from .chains import stamp_verdict
            stamp_verdict(rows, case)
        persist(rows)
        log.info("[%s] %s r%d done (%d rows)", label, case["id"], repeat, state["rows"])

    # perf first, serially, with a warmup so the first TTFT isn't cold-start
    perf_cases = ([c for c in cases if c["category"] == "perf"]
                  if not recover_mode and perf else [])
    other_cases = [c for c in cases if c["category"] != "perf"]
    # 4.5 chains (chains.py): one placeholder per chain; its attempts are scheduled as
    # they unlock, inside this run-start round (the early stop sees them)
    chain_cases = [c for c in other_cases if c.get("chain_spec")]
    other_cases = [c for c in other_cases if not c.get("chain_spec")]
    if perf_cases:
        t0, p0 = ctx.sampling({})
        ctx.call([{"role": "user", "content": "Hi"}], max_tokens=8,
                 temperature=t0, top_p=p0, seed=42, recover=False)
        # serial (one worker), but on a pool the main thread can leave on ABORT
        _run_pool([(run_case_repeat, (case, repeat, None), (case, repeat))
                   for case in perf_cases
                   for repeat in range(1, repeats_for(cfg, "perf", smoke) + 1)], 1, abort, label)
        # run-start sanity (2026-11-14): a probe that hit its ceiling, looped into one repeated
        # character / short n-gram, or gave no answer means a broken instance; stop before any
        # scored item is sent (a degenerate 8B stored zeros as scores twice)
        if earlystop.settings(cfg).get("enabled", True) and not ABORT.is_set():
            with ctx.lock:
                bad = degenerate.probe_failure(list(perf_seen))
            if bad:
                trip = {**bad, "after_answers": 0, "bench_run_id": bench_run_id}
                log.error("[%s] %s — stopping this model", label, earlystop.describe(trip))
                raise EarlyStopError(trip)

    if recover_mode:
        # job unit = the exact (run id, case, repeat) triple, so the same case
        # overflowing in two accumulated runs is re-run for each of them
        by_id = {c["id"]: c for c in other_cases}
        jobs = [(by_id[cid], rep_, rid) for rid, cid, rep_
                in sorted(only_jobs, key=lambda j: (str(j[0]), j[1], j[2])) if cid in by_id]
        missing = sorted((j for j in only_jobs if j[1] not in by_id),
                         key=lambda j: (str(j[0]), j[1], j[2]))
        what = f"escalation to {rung} tokens" if rung else (record_key or "recover")
        for j in missing:
            log.warning("%s: %s r%s (run %s) is not in the selected case set — skipped",
                        what, j[1], j[2], j[0])
        log.info("%s: %d job(s) to re-run for %s (%d not selectable)",
                 what, len(jobs), label, len(missing))
        if chain_cases:
            log.warning("%s: %d chain(s) are not run in a %s round", label, len(chain_cases), what)
            chain_cases = []
    else:
        jobs = [(c, r, None) for c in other_cases
                for r in range(1, repeats_for(cfg, c["category"], smoke, c) + 1)]
    jobs = schedule_jobs(jobs, _job_seconds_fn(label, other_cases,
                                               getattr(provider, "type", None) in _LOADING_TYPES))
    if cfg["defaults"].get("judged_first"):
        jobs = judged_first(jobs)       # 4.5: the judge overlaps generation
    driver = more = None
    chain_starts: list = []
    if chain_cases:
        from . import chains as _chains
        # resume: a stored, still-current graded attempt is not presented again
        driver = _chains.Driver(chain_cases, prior_rows=load_transcripts(label))
        chain_starts = [(i, 1, None) for i in driver.start()]
        # 4.5 bank: a routed module unit's route rides from its placeholder onto every row
        routes = {c["id"]: c["route"] for c in chain_cases if c.get("route")}

        def run_chain_attempt(inst):
            try:
                case_ = _chains.materialize(inst)
            except Exception as e:  # noqa: BLE001 - a broken generator halts its chain, not the run
                log.error("[%s] %s: the instance could not be generated (%s) — chain halted",
                          label, inst["id"], e)
                with ctx.lock:
                    state["case_errors"] += 1
                return
            if routes.get(inst["chain"]["id"]):
                case_["route"] = routes[inst["chain"]["id"]]
            run_case_repeat(case_, 1, None)

        def more(key):
            inst, _rep = key
            if not inst.get("chain"):
                return []
            with ctx.lock:
                rows_ = chain_rows.pop(inst["id"], None)
            return [(run_chain_attempt, (nxt,), (nxt, 1)) for nxt in driver.record(inst["id"], rows_)]
        log.info("%s: %d chain(s), %d first attempt(s) to run", label, len(chain_cases),
                 len(chain_starts))
    workers = (ctx.placement.pool.total_slots() if ctx.placement is not None
               else provider.workers(model_id))
    warnings: list[str] = []
    if ctx.thinking and workers <= FEW_SLOTS_WARN and len(jobs) > workers:
        msg = (f"thinking model on only {workers} parallel slot(s) — {len(jobs)} jobs with "
               f"up to {ctx.budget(6144)}-token budgets will queue behind each other; "
               f"expect the run to overrun its time box")
        log.warning("!!! %s: %s", label, msg)
        warnings.append(msg)
        if not recover_mode:
            _write_meta(label, entry, provider, profile=cfg.get("_profile"),
                        warnings=warnings, revision=_revision(cfg))
    if workers > 1:
        log.info("running %d jobs with concurrency=%d", len(jobs), workers)
    # longest first (ship plan C8): chain first attempts and static jobs in ONE order by expected
    # completion tokens (the model's own stored mean per cell, else the ceiling), so the long
    # poles start first; deeper rungs are still queued as they unlock
    try:
        prior = tokens_prior(load_transcripts(label))
    except Exception as e:  # noqa: BLE001 - no stored rows: every unit at its ceiling
        log.debug("no token estimate for %s (%s)", label, e)
        prior = {}
    calls = [(run_chain_attempt, (u[1],), (u[1], 1)) if u[0] == "chain"
             else (run_case_repeat, (u[1], u[2], u[3]), (u[1], u[2]))
             for u in longest_first([i for i, _r, _rid in chain_starts], jobs, cfg, prior)]
    first_err = _run_pool(calls, max(1, workers), abort, label, more=more)
    if isinstance(first_err, (RunStopped, ModelRunError)):
        raise first_err
    if first_err and state["rows"] == 0:
        raise first_err

    marker_msg = ctx.marker_warning(state["rows"])
    if marker_msg:
        log.warning("!!! %s: %s", label, marker_msg)
        warnings.append(marker_msg)
    carry["thinking"] = ctx.thinking
    carry["thinking_source"] = getattr(ctx, "thinking_source", None)
    tr = ctx.thinking_record()
    if last:
        log.info("%s: thinking = %s (%s) — %s", label, tr["thinking"], tr["source"],
                 "one board budget for every row" if tr["one_budget"]
                 else ("thinking budget" if tr["thinking"] else "raw case budgets"))
    secs = round((datetime.now(timezone.utc) - t_round).total_seconds(), 1)
    if ctx.placement is not None:
        plan = {**(plan or {}), "placement": _placement_record(cfg, ctx.placement)}
    round_rec = {"round": round_name or "all", "ctx": ctx_len, "jobs": len(jobs),
                 "rows": state["rows"], "workers": workers, "load_s": round(load_s, 1),
                 "s": secs, "plan": plan or None}
    if driver is not None:
        round_rec["chains"] = driver.summary()
    if recover_mode:
        _write_recover_record(label, jobs=len(jobs), rows=state["rows"], failed=False,
                              key=record_key or ("escalated" if rung else "recovered"), rung=rung,
                              plan=plan or None, round=round_name, ctx=ctx_len, s=secs)
    else:
        prev = _read_meta(label)
        extra = {"rounds": list(prev.get("rounds") or []) + [round_rec] if not reset
                 else [round_rec],
                 "timing": list(prev.get("timing") or []) + [
                     {"phase": f"generation round {round_name}" if round_name else "generation",
                      "s": secs, "ctx": ctx_len, "jobs": len(jobs), "workers": workers}],
                 "thinking_detect": tr}
        if es is not None and last:
            # what the check saw (misrouted counted, reported only); an
            # unusable check is an error line and blocks publish (fail closed)
            extra["early_stop"] = es.record()
            if es.error:
                extra["publishable"] = False
                warnings.append(es.error)
        if last:
            # the top-level plan/context stay the FIRST round's (the standard
            # rows); every round is listed under ``rounds``
            _write_meta(label, entry, provider, load_s=None if not reset else load_s,
                        bench_run_id=bench_run_id, finished=True,
                        profile=cfg.get("_profile"), plan=(plan or None) if reset else None,
                        ctx_len=ctx_len if reset else None, warnings=warnings or None,
                        revision=_revision(cfg), regime=ctx.regime_stamp(), extra=extra)
        else:
            _write_meta(label, entry, provider, profile=cfg.get("_profile"),
                        revision=_revision(cfg), warnings=warnings or None, extra=extra)
    return {"failed": False, "rows": state["rows"], "load_s": round(load_s, 1),
            "round": round_name, "ctx": ctx_len,
            "device": provider.name, "skipped": state["skipped"],
            "case_errors": state["case_errors"], "jobs": len(jobs),
            "plan": plan or None, "workers": workers, "warnings": warnings,
            "cost_usd": round(state["cost"], 4) if entry.get("price") else None,
            "rig_minutes": round(state["rig_min"], 3) if state.get("rig_min") else None}


def _placement_record(cfg: dict, placement) -> dict:
    """The meta record of a declared placement: every copy (live plan, jobs, fate) and the
    judge schedule DERIVED from it (subject_copies.judge_mode)."""
    rec = placement.record()
    try:
        j = _subject_copies.judge_instance(cfg)
    except Exception as e:  # noqa: BLE001 - the record never stops a run
        log.debug("judge placement unreadable: %s", e)
        j = None
    rec["judge_mode"] = _subject_copies.judge_mode(placement.copies,
                                                   (j or {}).get("instance_devices"))
    if j:
        rec["judge_devices"] = j.get("instance_devices")
    return rec


def _run_pool(calls: list[tuple], workers: int, abort: threading.Event, label: str,
              more=None):
    """Run ``fn(*args)`` for every ``(fn, args, key)`` on ``workers`` threads
    and return the first RunStopped / ModelRunError / transport error (None
    when all finished). A ModelRunError sets ``abort`` (this model's
    remaining jobs drain; the batch goes on), and the queue is not drained
    for it — every remaining job returns at once.

    The main thread waits in short polls instead of ``as_completed`` inside
    ``with ThreadPoolExecutor``: on a hard stop (ABORT) it cancels the queued
    jobs and leaves WITHOUT joining the workers. A worker blocked in a socket
    to an unreachable rig used to keep the ``with`` block (and so the whole
    process) alive until systemd SIGABRTed it (STEP4 defect 1).

    ``more(key)`` (4.5 chains): called on the main thread after a job finished without
    an error; the ``(fn, args, key)`` calls it returns are queued behind everything
    already queued (a chain's next rung)."""
    if not calls:
        return None
    pool = ThreadPoolExecutor(max_workers=max(1, workers),
                              thread_name_prefix=f"cf-{label}"[:40])
    futs = {pool.submit(fn, *args): key for fn, args, key in calls}
    pending = set(futs)
    first_err = None
    try:
        while pending:
            done, pending = _fwait(pending, timeout=POOL_POLL_S, return_when=FIRST_COMPLETED)
            for fut in done:
                if fut.cancelled():
                    continue
                try:
                    fut.result()
                    if more is not None and not abort.is_set() and not ABORT.is_set():
                        for fn, args, key in more(futs[fut]):
                            nf = pool.submit(fn, *args)
                            futs[nf] = key
                            pending.add(nf)
                except RunStopped as e:
                    first_err = first_err or e
                except ModelRunError as e:
                    first_err = first_err or e
                    abort.set()  # drain THIS model's workers; the batch goes on
                except (TransportError, WrongModelError) as e:  # defensive: handled per case
                    case, r = futs[fut]
                    log.error("[%s] %s r%d unhandled transport failure: %s", label, case["id"],
                              r, e)
                    first_err = first_err or e
            if ABORT.is_set():
                n = sum(1 for f in pending if not f.done())
                log.warning("[%s] stopping — %d job(s) dropped (in flight or queued), "
                            "not waiting for them", label, n)
                first_err = RunStopped("stopped (SIGTERM)")
                break
    finally:
        if ABORT.is_set():
            pool.shutdown(wait=False, cancel_futures=True)
        else:
            pool.shutdown(wait=True, cancel_futures=True)
    if ABORT.is_set() and not isinstance(first_err, RunStopped):
        first_err = RunStopped("stopped (SIGTERM)")
    return first_err


def _capped_empty(r) -> bool:
    """A completion that ran to its token cap (finish=length) with no visible
    answer and no tool call: the thinking block never closed (2026-11-14
    thinking fallback). A capped reply WITH visible text is not this."""
    return (getattr(r, "finish_reason", None) == "length"
            and not (getattr(r, "response_text", "") or "").strip()
            and not getattr(r, "tool_calls", None))


def _empty_stop(r) -> bool:
    """A completion that finished normally (finish=stop) with no answer and no
    tool call — reasoning may be present (a model that stopped inside its
    thinking); an overflow (finish=length) is NOT this: it counts as ran out."""
    return (getattr(r, "finish_reason", None) == "stop"
            and not (getattr(r, "response_text", "") or "").strip()
            and not getattr(r, "tool_calls", None))


def retry_seed(seed, attempt: int) -> int:
    """The seed of retry ``attempt`` (1, 2, ...) of a request sent with ``seed``: different
    from it and from every other attempt, and the same on every re-run of the case (an empty
    stop's regenerate must be a new sample; a seeded server repeats the old one)."""
    return (int(seed) + 7919 * int(attempt)) % (2 ** 31 - 1)


def _degenerate_result(r) -> dict | None:
    """degenerate.row_degenerate on a live result (one repeated character / short n-gram)."""
    try:
        return degenerate.row_degenerate({"response": getattr(r, "response_text", "") or "",
                                          "reasoning": getattr(r, "reasoning_text", "") or "",
                                          "finish_reason": getattr(r, "finish_reason", None),
                                          "tool_calls": getattr(r, "tool_calls", None)})
    except Exception:  # noqa: BLE001 - the test never costs a row
        return None


def _blank_view(result: ChatResult) -> ChatResult:
    """4.5 (``empty_stop_rule``): what the graders read for an empty stop or a degenerate reply
    -- nothing (Jake: grade only the response given; a loop is a failure, like a blank)."""
    return _dc_replace(result, response_text="", reasoning_text="")


def _empty_stop_stamp(ctx, result) -> dict | None:
    """The ``empty_stop`` row stamp under the 4.5 ``empty_stop_rule`` (None otherwise):
    finish=stop, no visible content, no tool call. A llama-server reasoning parser files an
    unclosed think block wholly as reasoning_content (no tag survives), so this is how
    "reasoning not closed" reads from a parsed reply; a model that left a whole answer there
    (Nanbeige) has the same shape and is not told apart here (2026-11-14 reference rows:
    reasoning cut mid-word, finish=stop, 5-2087 tokens, turns 7-12 at 9 slots)."""
    if not getattr(ctx, "empty_stop_rule", False) or not _empty_stop(result):
        return None
    rt = result.reasoning_text or ""
    return {"reasoning_chars": len(rt),
            "completion_tokens": getattr(result, "completion_tokens", None),
            "reasoning_tail": rt[-60:]}


def needs_judging(case: dict) -> bool:
    """Whether a case's rows go to the judge (it names a rubric or carries
    judge elements). A reference-graded case that only learns at grading
    time that it needs the judge is not counted."""
    return bool(case.get("rubric") or case.get("judge_elements"))


def judged_first(jobs: list[tuple]) -> list[tuple]:
    """Stable partition: cases that need judging first, so the judge overlaps
    the rest of generation. The order inside each group is unchanged."""
    return ([j for j in jobs if needs_judging(j[0])]
            + [j for j in jobs if not needs_judging(j[0])])


def schedule_jobs(jobs: list[tuple], seconds=None) -> list[tuple]:
    """Code-bank rows first (3.9, M7: the longest single jobs), then
    multi-turn sessions, then the longest categories (``CATEGORY_ORDER``),
    profile order within a category — a stable sort, so ids and repeats keep
    their relative order.

    4.0 (review M5, the gemma test: the two 25-min writing sessions started
    5 min late behind shorter jobs): with ``seconds(case)`` — the job's
    expected SEQUENTIAL time (estimate.job_seconds, the model's measured
    timing) — the longest job starts first (LPT), the rule above only breaks
    ties."""
    rank = {c: i for i, c in enumerate(CATEGORY_ORDER)}

    def base(j):
        return (0 if j[0]["category"] in BANK_CATEGORIES else 1 if j[0].get("turns") else 2,
                rank.get(j[0]["category"], len(rank)))
    if seconds is None:
        return sorted(jobs, key=base)
    return sorted(jobs, key=lambda j: (-float(seconds(j[0]) or 0.0), base(j)))


_CELL_TAIL = re.compile(r"-\d+$")


def unit_key(u: tuple) -> str:
    """The estimate key of a dispatch unit: ``cell:<chain id less its -NNN>`` for a chain attempt (one
    cell = one generated set, its units alike), ``case:<id>`` for a static job."""
    if u[0] == "chain":
        return "cell:" + _CELL_TAIL.sub("", u[1]["chain"]["id"])
    return "case:" + u[1]["id"]


def tokens_prior(rows: list[dict]) -> dict[str, float]:
    """{unit_key: mean completion tokens per unit} from a model's stored rows: a unit's rows (one
    run, one unit, one repeat; a session's turns) summed, then the mean over that cell's units.
    Error, skipped and perf rows are no estimate."""
    per: dict[tuple, float] = {}
    for r in rows:
        if r.get("error") or r.get("skipped") or r.get("category") == "perf":
            continue
        n = (r.get("metrics") or {}).get("completion_tokens")
        if not isinstance(n, (int, float)):
            continue
        ch = r.get("chain") or {}
        key = (unit_key(("chain", {"chain": ch})) if ch.get("id") else "case:" + str(r.get("case_id")))
        u = (key, r.get("bench_run_id"), ch.get("id") or r.get("case_id"), r.get("repeat"))
        per[u] = per.get(u, 0.0) + float(n)
    acc: dict[str, list[float]] = {}
    for (key, *_rest), v in per.items():
        acc.setdefault(key, []).append(v)
    return {k: sum(v) / len(v) for k, v in acc.items()}


def expected_tokens(u: tuple, cfg: dict, prior: dict[str, float]) -> float:
    """A unit's expected completion tokens: the model's own stored mean for its cell/case, else its
    output ceiling (a chain attempt's per-request ``ceiling``; a static job's fixed budget, e.g. the
    bank's ``bank_budget``; else its own ``max_tokens``; else 0)."""
    k = unit_key(u)
    if k in prior:
        return float(prior[k])
    if u[0] == "chain":
        from .chains import MAX_PROMPT_CEILING
        return float(u[1]["chain"].get("ceiling") or MAX_PROMPT_CEILING)
    case = u[1]
    return float(fixed_budget(cfg, case) or case.get("max_tokens") or 0)


def longest_first(chain_starts: list[dict], jobs: list[tuple], cfg: dict,
                  prior: dict[str, float]) -> list[tuple]:
    """ONE dispatch order for a run (ship plan C8): chain first attempts (``("chain", inst)``) and static
    jobs (``("job", case, repeat, run_id)``) by expected completion tokens, longest first, so the long
    poles (code-bank calls, 35-65k tokens) overlap the rest instead of starting last and running alone.
    Ties: bank rows first, then the order given (chain starts, then ``jobs`` as already scheduled)."""
    units = [("chain", i) for i in chain_starts] + [("job", *j) for j in jobs]

    def key(iu):
        i, u = iu
        bank = u[0] == "job" and u[1].get("category") in BANK_CATEGORIES
        return (-expected_tokens(u, cfg, prior), 0 if bank else 1, i)
    return [u for _i, u in sorted(enumerate(units), key=key)]


def makespan(units: list[tuple], slots: int, duration) -> float:
    """List scheduling of ``units`` in order on ``slots`` workers, ``duration(unit)`` each: the finish
    time of the last one (what the dispatch order is judged by)."""
    import heapq
    free = [0.0] * max(1, slots)
    end = 0.0
    for u in units:
        t = heapq.heappop(free) + float(duration(u))
        end = max(end, t)
        heapq.heappush(free, t)
    return end


def _job_seconds_fn(label: str, cases: list[dict], rig: bool):
    """case -> expected sequential seconds for ``label`` (measured timing
    where it exists, category defaults otherwise); None when unavailable."""
    try:
        from . import estimate as _est
        from .config import load_cases
        have = {c["id"] for c in cases}
        pool = cases + [c for c in load_cases() if c["id"] not in have]
        measured = _est.measured_speed(label, pool, rig)
        return lambda c: _est.job_seconds(c, rig, measured)
    except Exception as e:  # noqa: BLE001 — scheduling falls back to the category order
        log.debug("no job-time estimate for %s (%s)", label, e)
        return None


def _read_meta(label: str) -> dict:
    try:
        return json.loads((results_dir() / f"meta_{label}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_recover_record(label: str, key: str = "recovered", **fields) -> None:
    """``crucibleforge recover`` never rewrites a run's meta (failed/error/
    bench_run_id/profile belong to the ORIGINAL run); it appends its own
    record under ``recovered`` instead (an escalation round: ``escalated``)."""
    path = results_dir() / f"meta_{label}.json"
    meta = {}
    if path.exists():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    rec = {"ts": _now(), **{k: v for k, v in fields.items() if v is not None}}
    meta.setdefault(key, []).append(rec)
    results_dir().mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _generation_reject_kind(error: str) -> str:
    """``template`` when the server could not even RENDER the request with the
    model's chat template ("Unable to generate parser for this template" —
    precog-123b's template raises on a ``tool`` role message): nothing was
    generated, so it is errored like a rejected request, not a model failure
    (maintainer 2026-09-24: errored rows are excluded, never scored 0).
    ``generation`` otherwise (malformed tool-call JSON the model wrote)."""
    return "template" if "unable to generate parser" in error.lower() else "generation"


def _error_row(base_row: dict, case: dict, error: str, kind: str = "generation") -> dict:
    """A persisted row for a case whose generation never arrived.

    ``kind="generation"``: the server could not deliver what the MODEL
    produced (malformed tool-call JSON) — the case FAILS and counts against
    the model. ``kind="transport"``/``"template"``: the server or the request
    failed (connection, 5xx, a rejected request, a chat template the server
    cannot render) — the row is ERRORED (grade ``error``): excluded from the score, listed in
    failures.md, never judged."""
    first_user = (case.get("tool_script") or [{}])[0].get("user", "")
    row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
           "system": case.get("system"),
           "prompt": case.get("prompt") or first_user,
           "response": "", "reasoning": "", "tool_calls": [], "finish_reason": "error",
           "truncated": False, "error": error[:2000], "error_kind": kind, "metrics": {}}
    if kind in ("transport", "template", "ctx_short"):
        row["grade"] = "error"
        row["grade_detail"] = error[:300]
        return row
    if case.get("grader") or case.get("tool_script") or case.get("env") or case.get("console"):
        row["grade"] = "fail"
        row["grade_detail"] = error[:300]
        if case.get("grader") == "repo_tests" or case.get("env") or case.get("console"):
            row["score"] = 0.0
    if case.get("rubric") or case.get("turns"):
        # judged case: nothing to judge — recorded as an empty generation
        row["needs_judge"] = True
        row["rubric"] = case.get("rubric") or ("rp_multi" if case.get("turns") else None)
        if rpdeep.is_programmatic(case):
            row["rubric"] = None
            row["judge"] = {**copy.deepcopy(rpdeep.PROGRAMMATIC_VERDICT), "empty_generation": True}
    return row


def overflow_jobs(rows: list[dict]) -> set[tuple]:
    """(bench_run_id, case_id, repeat) of every job with a reasoning-overflow
    row (finish=length, empty content, reasoning present) that has not been
    through recovery yet. Perf rows are timing rows and are never re-run."""
    out: set[tuple] = set()
    for r in rows:
        if r.get("category") == "perf" or r.get("skipped"):
            continue
        if r.get("recovery") is not None:
            continue  # already went through the ladder (recovered or not)
        if (r.get("finish_reason") == "length" and not (r.get("response") or "").strip()
                and (r.get("reasoning") or "").strip()):
            out.add((r.get("bench_run_id"), r.get("case_id"), r.get("repeat")))
    return out


def escalation_rungs(cfg: dict) -> list[int]:
    """The profile's ascending output budgets for the escalation round (empty
    list = off). Only rungs above the board cap mean anything."""
    d = cfg.get("defaults") or {}
    raw = d.get("escalation_rungs", DEFAULT_ESCALATION_RUNGS)
    cap = int(d.get("thinking_max_tokens_cap", 32768))
    return sorted({int(x) for x in (raw or []) if int(x) > cap})


#: A rung that failed this many times since the label's last board run is given
#: up (a refusal because the rig cannot load the context counts as all of them):
#: the nightly's done-check must converge, not re-try an unservable rung forever.
ESCALATION_MAX_FAILURES = 2


def blocked_rungs(label: str) -> set[int]:
    """Rungs this label's escalation gave up on: failed ``escalated`` records
    in its meta since its latest board run started (``started``), counted per
    rung; an unservable-context refusal ("needs ctx") blocks at once. A new
    board run starts the count again (the rig, or the registry, may have
    changed)."""
    path = results_dir() / f"meta_{label}.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    since = str(meta.get("started") or "")
    fails: dict[int, int] = {}
    for rec in meta.get("escalated") or []:
        if not isinstance(rec, dict) or not rec.get("failed") or not rec.get("rung"):
            continue
        if since and str(rec.get("ts") or "") < since:
            continue
        if "stopped by user" in str(rec.get("error") or "") or \
                str(rec.get("error") or "").startswith(CAP_STOP):
            continue                      # a STOP / window edge / spend cap is not the rung's fault
        weight = ESCALATION_MAX_FAILURES if "needs ctx" in str(rec.get("error") or "") else 1
        fails[int(rec["rung"])] = fails.get(int(rec["rung"]), 0) + weight
    return {r for r, n in fails.items() if n >= ESCALATION_MAX_FAILURES}


def escalated_rungs(label: str) -> set[int]:
    """Rungs an `escalate` run of this label completed (meta ``escalated``
    records that did not fail). The Room view accepts rows at these rungs even
    above the profile's own top rung (an explicit `escalate --rungs 131072`)."""
    path = results_dir() / f"meta_{label}.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {int(rec["rung"]) for rec in meta.get("escalated") or []
            if isinstance(rec, dict) and rec.get("rung") and not rec.get("failed")}


def open_rungs(label: str, rungs: list[int]) -> list[int]:
    """``rungs`` minus the ones :func:`blocked_rungs` gave up on (ascending)."""
    blocked = blocked_rungs(label)
    return sorted(r for r in rungs if r not in blocked)


def escalation_todo(rows: list[dict], label: str, rungs: list[int]) -> set[tuple]:
    """The jobs still due for ``label``: out of budget below its highest rung
    that is still open. Empty when every rung was given up on - so a model
    whose rig cannot serve 128k is done at 64k, and the plan converges."""
    usable = open_rungs(label, rungs)
    return escalation_jobs(rows, max(usable)) if usable else set()


def escalation_jobs(rows: list[dict], rung: int) -> set[tuple]:
    """(None, case_id, repeat) of every job that ran out of budget below
    ``rung``. ``rows`` = the label's Room view (report.board_filter with
    ``with_rungs``: the newest run of each job, an earlier rung's included), so
    a job a 64k rung already finished is not re-run at 128k. A job ran out
    when any of its rows (any turn of a session) ended finish=length while the
    model was thinking: a non-thinking model's case budget is the case's own
    design and a bigger cap would not change what is sent. Perf rows are
    timing rows and skipped rows never ran."""
    out: set[tuple] = set()
    for r in rows:
        if r.get("category") == "perf" or r.get("skipped") or r.get("grade") == "error":
            continue
        if r.get("category") in BANK_CATEGORIES:   # one budget, never escalated
            continue
        if r.get("finish_reason") != "length":
            continue
        # a non-thinking row's budget was the case's own design — except
        # under the one-budget rule (deployed regime, S8), where every model
        # got the board budget and a bigger rung changes what is sent
        if r.get("thinking") is not True and r.get("regime") != REGIME_DEPLOYED:
            continue
        if int(r.get("budget_rung") or 0) >= rung or int(r.get("max_tokens_sent") or 0) >= rung:
            continue
        out.add((None, r.get("case_id"), r.get("repeat")))
    return out


def escalate_models(cfg: dict, model_entries: list[dict], cases: list[dict],
                    rungs: list[int] | None = None, room_rows=None) -> dict:
    """Run every escalation rung in turn over the jobs that ran out of budget
    (see DEFAULT_ESCALATION_RUNGS). ``room_rows(label)`` = the Room view
    (default report.board_filter(..., with_rungs=True)), read again before
    each rung so what a lower rung finished is not re-run. Returns
    {rung: run_models summary}. The rows need the judge like any other."""
    if room_rows is None:
        from . import report

        def room_rows(label):
            return report.board_filter(label, cfg, with_rungs=True, sampler_view="current")
    rungs = sorted(rungs if rungs is not None else escalation_rungs(cfg))
    out: dict[int, dict] = {}
    for rung in rungs:
        if STOP.is_set():
            break
        jobs_by_label: dict[str, set] = {}
        for e in model_entries:
            if rung in blocked_rungs(e["name"]):
                log.info("%s: escalation to %d tokens given up (failed %d time(s) since its "
                         "last board run)", e["name"], rung, ESCALATION_MAX_FAILURES)
                continue
            found = escalation_jobs(room_rows(e["name"]), rung)
            log.info("%s: %d job(s) ran out of budget below %d tokens", e["name"], len(found), rung)
            if found:
                jobs_by_label[e["name"]] = found
        if not jobs_by_label:
            continue
        entries = [e for e in model_entries if e["name"] in jobs_by_label]
        out[rung] = run_models(cfg, entries, cases, jobs_by_label=jobs_by_label, rung=rung)
    return out


def recover_models(cfg: dict, model_entries: list[dict], cases: list[dict]) -> dict:
    """Re-run only the reasoning-overflow jobs of each model (same bench_run_id,
    so the recovered rows supersede the empty ones); the fresh rows carry no
    judge verdict, so ``crucibleforge judge`` re-scores them."""
    from .config import load_transcripts
    jobs_by_label: dict[str, set] = {}
    for e in model_entries:
        found = overflow_jobs(load_transcripts(e["name"]))
        if found:
            jobs_by_label[e["name"]] = found
        log.info("%s: %d reasoning-overflow job(s) to recover", e["name"], len(found))
    entries = [e for e in model_entries if e["name"] in jobs_by_label]
    if not entries:
        return {}
    return run_models(cfg, entries, cases, jobs_by_label=jobs_by_label)


def _answer_view(result: ChatResult) -> ChatResult:
    """What the graders may read. ``scoreable_text`` falls back to the
    reasoning channel when content is empty — right for a server that
    misrouted a FINISHED answer (finish=stop), wrong for a reasoning overflow
    (finish=length): code dug out of 100k chars of cut-off chain-of-thought
    was never delivered to anyone (9 of one model's 51 coding 'passes'
    on 2026-08-22 were exactly that). Overflows are recovered upstream; an
    unrecovered one is graded on its (empty) content."""
    if result.finish_reason == "length" and not result.response_text.strip():
        return _dc_replace(result, reasoning_text="")
    return result


def _annotate_recovery(row: dict, result: ChatResult) -> None:
    """Carry the reasoning-overflow record (and the empty-stop retry flag,
    W5 2026-11-01: it was only on the in-memory result, so a stored row could
    not show whether the retry ran) onto the transcript row."""
    if getattr(result, "empty_retry", False):
        row["empty_retry"] = True
    if getattr(result, "thinking_fallback", None):
        row["thinking_fallback"] = result.thinking_fallback
    if getattr(result, "empty_stop_retry", None):
        row["empty_stop_retry"] = result.empty_stop_retry
        if result.empty_stop_retry.get("answered_thinking_off"):
            # 4.5: the graded reply came from the thinking-off attempt (board count)
            row["answered_thinking_off"] = True
    if result.recovery is None:
        return
    row["reasoning_overflow"] = True
    row["recovery"] = result.recovery


BANK_CATEGORIES = ("codebank", "codeagent", "codebank_long", "codeagent_long")
#: chat template + tokenizer-estimate slack when clamping a budget to the window
BANK_WINDOW_SLACK = 1536


def bank_budget(ctx, prompt_tokens: int) -> int | None:
    """The code bank's single generous budget for one request: the profile's
    ``bank_budget`` (65,536), clamped so prompt + budget fits the model's
    context window. None when the bank budget is off. The row records what
    was really sent (``max_tokens_sent``)."""
    if not getattr(ctx, "bank_budget", 0):
        return None
    b = int(ctx.bank_budget)
    if getattr(ctx, "ctx_len", None):
        b = min(b, int(ctx.ctx_len) - int(prompt_tokens) - BANK_WINDOW_SLACK)
    return max(b, 256)


def row_budget(ctx, case: dict, prompt_tokens: int) -> int | None:
    """The exact budget a row is sent when the case has a fixed one
    (:func:`fixed_budget`: bank rows -> bank_budget, a ladder set's
    ``ladder_budget``), clamped to the window like the bank's; None = the
    board regime (``ctx.budget``). An escalation rung decides its own
    budget, so a ladder budget never applies inside a rung round."""
    if case.get("category") in BANK_CATEGORIES:
        return bank_budget(ctx, prompt_tokens)
    if case.get("chain") and getattr(ctx, "ctx_len", None):
        # 4.5 chains (R23): no scoring budget; the attempt's ceiling = the profile's per-family
        # `ceiling` (the model's output maximum), never above the served window minus the
        # prompt. A cut there is a failed attempt ("ran out"), never a budget rule.
        b = int(ctx.ctx_len) - int(prompt_tokens) - BANK_WINDOW_SLACK
        if (case["chain"].get("ceiling")):
            b = min(b, int(case["chain"]["ceiling"]))
        return max(b, 256)
    lb = case.get("ladder_budget") or case.get("budget")
    if not lb or getattr(ctx, "rung", None):
        return None
    b = int(lb)
    if getattr(ctx, "ctx_len", None):
        b = min(b, int(ctx.ctx_len) - int(prompt_tokens) - BANK_WINDOW_SLACK)
    return max(b, 256)


def _prompt_tokens(messages: list[dict]) -> int:
    return int(sum(len(m.get("content") or "") + len(m.get("reasoning_content") or "")
                   for m in messages) / 3.5)


def _run_single(case, base_row, ctx: _Ctx, max_tokens, temperature, top_p, seed) -> dict:
    msgs = _messages_for(case)
    exact = row_budget(ctx, case, _prompt_tokens(msgs))
    sent = exact or ctx.budget(max_tokens)
    result = ctx.call(msgs, max_tokens=max_tokens, exact_budget=exact,
                      recover=ctx.recover_for(case),
                      **({"regen": ctx.regen_for(case)} if hasattr(ctx, "regen_for") else {}),
                      temperature=temperature, top_p=top_p, seed=seed,
                      tools=case.get("tools"))
    if (case.get("category") in BANK_CATEGORIES and not result.finish_reason
            and not (result.response_text or "").strip() and not result.tool_calls):
        # the stream ended with no finish reason and no answer: the server or
        # the connection dropped it (pilot 2026-10-26: 82 s of reasoning, then
        # nothing). That is not the model failing the task.
        raise TransportError("stream ended without a finish reason and without an answer")
    row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
           "system": case.get("system"),
           "prompt": case["prompt"], "response": result.response_text,
           "reasoning": result.reasoning_text, "tool_calls": result.tool_calls,
           "finish_reason": result.finish_reason,
           "truncated": result.finish_reason == "length",
           "max_tokens_sent": sent,
           # a tool call with empty content is a normal answer, not a misroute
           "answered_in_reasoning": in_reasoning_flag(result.response_text,
                                                      result.reasoning_text,
                                                      result.tool_calls),
           "metrics": _metrics(result)}
    es_stamp = _empty_stop_stamp(ctx, result)
    if es_stamp is not None:
        row["empty_stop"] = es_stamp
    _annotate_recovery(row, result)
    blank = getattr(ctx, "empty_stop_rule", False) and (es_stamp is not None or _degenerate_result(result))
    grade_single_row(row, result, case, blank, sent)
    return row


def grade_single_row(row: dict, result: ChatResult, case: dict, blank: bool, sent) -> None:
    """The grade of one single-turn reply, written on its row: the live run and the regrade
    of a stored row (crucibleforge.regrade) share this code, so a regrade cannot drift from a
    fresh run. ``blank``: graded as an empty answer (4.5 empty-stop / degenerate rule)."""
    verdict = grade(_blank_view(result) if blank else _answer_view(result), case)
    if verdict:
        row["grade"] = verdict["grade"]
        detail = verdict["detail"]
        if verdict["grade"] == "fail" and result.finish_reason == "length":
            # be honest about WHY: a truncated reply is a budget/verbosity
            # failure, not necessarily a wrong answer
            reas = (f"{result.reasoning_tokens} reasoning tokens" if result.reasoning_tokens
                    else f"~{len(result.reasoning_text) // 4} reasoning tokens (est.)"
                    if result.reasoning_text else "no reasoning channel")
            detail = f"TRUNCATED at {sent} tokens ({reas}); {detail}"
        row["grade_detail"] = detail
        if verdict.get("results") is not None:
            # per-element verdicts (grade_checks / positional exact): the case
            # grade stays strict, the elements say how much was right
            row["elements"] = {"results": verdict["results"], "rate": verdict.get("rate")}
        # code bank: gated 0..1 score, edit-format problems, sandbox stamp
        for k in ("score", "format_errors", "applied", "env", "tamper"):
            if verdict.get(k) is not None:
                row[k] = verdict[k]
        if verdict.get("needs_judge"):
            # reference-answer grading: a (small) LLM judge compares the
            # model's answer to the gold answer — see judge.RUBRICS["reference"]
            row["needs_judge"] = True
            row["rubric"] = "reference"
            row["reference"] = verdict.get("reference")
    if case.get("rubric"):
        row["needs_judge"] = True
        row["rubric"] = case["rubric"]
    if case.get("judge_key"):
        row["judge_key"] = case["judge_key"]
    if case.get("judge_card"):
        row["judge_card"] = case["judge_card"]      # 4.5 adult page: what its judge prompt and scorers read
    # deterministic identity / continuity / constraint checks (session_checks)
    chk = session_checks.run_checks(case, [_answer_view(result).response_text])
    if chk:
        row["checks"] = chk
    # objective prose metrics for creative writing (de-loads the judge)
    if case["category"] in CREATIVE_CATEGORIES and result.response_text.strip():
        row["prose"] = prose_metrics(result.response_text)


def _sendable_history(history: list[dict]) -> list[dict]:
    """The session history as it is SENT for the next turn. An assistant turn
    that stands empty (after the one retry) is not sent as an empty assistant
    message — strict chat templates and hosted APIs reject or mangle one. It
    is left out and the user turns on either side of it are joined (blank
    line), so roles keep alternating and nothing is invented: the model sees
    what the user said and no reply of its own. The RECORDED history (and so
    the judged conversation) keeps the empty turn as it happened.

    A turn that carries the reasoning channel but no visible text is NOT
    dropped: the chain is the model's own context and dropping it loses the
    thread (only a provider with reasoning replay on ever records one)."""
    out: list[dict] = []
    for m in history:
        if (m["role"] == "assistant" and not (m.get("content") or "").strip()
                and not (m.get("reasoning_content") or "").strip()):
            continue
        if out and out[-1]["role"] == m["role"] == "user":
            out[-1] = {**out[-1], "content": f"{out[-1]['content']}\n\n{m['content']}"}
        else:
            out.append(m)
    return out


def reply_of(result: ChatResult) -> tuple[str, bool]:
    """(the turn's answer, read from the reasoning channel?) under the
    single-turn policy (:func:`api.reasoning_answer`): the content channel,
    or — for a FINISHED reply (finish=stop) with empty content and no tool
    call — the reasoning channel, thinking tags stripped. A finish=length
    reply with no content stays empty (an overflow is never an answer)."""
    if (result.response_text or "").strip():
        return result.response_text, False
    alt = reasoning_answer(result.response_text, result.reasoning_text,
                           result.finish_reason, result.tool_calls)
    return (alt, True) if alt else (result.response_text or "", False)


def assistant_turn(result: ChatResult, replay: bool) -> dict:
    """The assistant message as it goes back into the conversation.

    The content channel is the answer. The reasoning channel is NOT the
    answer, but on a provider with reasoning replay on it IS the model's
    context: MiniMax documents that the complete assistant message
    (reasoning_content included) must be passed back or the chain is lost
    (1/10 recall with it dropped vs 7/10 replayed). With the replay off the
    turn carries the content only -- byte-identical to a run without the
    switch.

    A reply that FINISHED with its whole answer in the reasoning channel
    (:func:`reply_of`: finish=stop, empty content, no tool call) goes back as
    that answer — the same text the graders and the judge read, so the next
    turn continues the conversation the board scores (dropping it left the
    model with no reply of its own and the user turns glued together). It is
    not replayed a second time as ``reasoning_content``.

    One builder for every caller (the multi-turn runner, the tool loop's
    prose steps and the conformance probe), so a probe can never measure a
    conversation the bench does not actually send.
    """
    text, from_reasoning = reply_of(result)
    turn = {"role": "assistant", "content": text}
    # 4.5: only a CLOSED block is replayed -- the reply has visible content or a tool
    # call after it. A reply with no content (cut at the cap, or stopped inside its
    # thinking) has an unclosed block; its fragment is never sent back as reasoning.
    closed = bool((result.response_text or "").strip() or result.tool_calls)
    if replay and result.reasoning_text and not from_reasoning and closed:
        turn["reasoning_content"] = result.reasoning_text
    return turn


def _judged_history(history: list[dict]) -> list[dict]:
    """The recorded history as the JUDGE reads it: content only. The
    reasoning channel rides in the REPLAYED history (it is the model's own
    context, not its answer) and never in the judged conversation."""
    if not any("reasoning_content" in m for m in history):
        return history
    return [{k: v for k, v in m.items() if k != "reasoning_content"} for m in history]


#: a chain turn is not sent when less than this fits the window after its prompt (the
#: session's context is exhausted: outcome "context", never a transport error)
CONTEXT_MIN_TURN = 256


def _turn_prompt_estimate(msgs: list[dict], prev: ChatResult | None, tail: list[dict]) -> int:
    """The prompt tokens a turn will take: the character estimate of the whole request, or,
    when the previous turn's served prompt is known, that count + its completion + the new
    user turn's estimate (the server's own count of the history, thinking included when the
    template keeps it), whichever is larger."""
    est = _prompt_tokens(msgs)
    if prev is not None and prev.prompt_tokens:
        est = max(est, int(prev.prompt_tokens) + int(prev.completion_tokens or 0) + _prompt_tokens(tail))
    return est


def _context_row(base_row, i, user_turn, why: str, info: dict) -> dict:
    """The row of a chain turn that could not be sent (or was refused by the server) because
    the session's context is exhausted: finish_reason "context", no reply, never an error."""
    return {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": i, "prompt": user_turn,
            "response": "", "reasoning": "", "tool_calls": [], "finish_reason": "context",
            "truncated": False, "max_tokens_sent": 0, "answered_in_reasoning": False,
            "metrics": {}, "context_exhausted": {"why": why, **info}}


from .replay_rule import replayed_digest  # noqa: E402


def _run_multiturn(case, base_row, ctx: _Ctx, max_tokens, temperature, top_p, seed) -> list[dict]:
    """Drive scripted user turns; per-turn rows carry metrics, the final row
    carries the whole conversation and goes to the judge.

    A 4.5 chain session accounts for its context: a turn whose prompt leaves less than
    ``CONTEXT_MIN_TURN`` of the window is not sent, a context-length refusal from the server
    is not a transport error, and a reply cut while its budget was clamped below the ceiling
    by the window is not "ran out" — each is stamped ``context_exhausted`` (outcome
    "context") and the session ends there; the unsent turns score 0."""
    rows: list[dict] = []
    history: list[dict] = []
    n_turns = len(case["turns"])
    chain = case.get("chain") or None
    ceiling = int(chain["ceiling"]) if chain and chain.get("ceiling") else None
    prev: ChatResult | None = None
    stopped = False

    def _finish(row: dict) -> None:
        """The final row carries the whole conversation, the checks and the judge request."""
        row["needs_judge"] = True
        row["rubric"] = case.get("rubric", "rp_multi")
        if rpdeep.is_programmatic(case):
            # scored from its checks alone: no rubric, a verdict stub so nothing waits for a judge
            row["rubric"] = None
            row["judge"] = copy.deepcopy(rpdeep.PROGRAMMATIC_VERDICT)
        row["conversation"] = (
            ([{"role": "system", "content": case["system"]}]
             if case.get("system") else []) + _judged_history(history))
        if case.get("judge_key"):
            row["judge_key"] = case["judge_key"]
        if case.get("judge_card"):
            row["judge_card"] = case["judge_card"]  # 4.5 adult page: what its judge prompt and scorers read
        if case.get("history"):
            row["history_len"] = len(case["history"])
        replies = [m["content"] for m in history if m["role"] == "assistant"]
        if stopped:
            # the session ended on its context: the turns never answered are empty replies
            replies = (replies + [""] * n_turns)[:n_turns]
        chk = session_checks.run_checks(case, replies)
        if chk:
            row["checks"] = chk
        written = "\n\n".join(r for r in replies if r.strip())
        if case["category"] in CREATIVE_CATEGORIES and written:
            row["prose"] = prose_metrics(written)

    for i, user_turn in enumerate(case["turns"], start=1):
        check_abort()   # a hard stop ends a session between turns, never mid-turn
        tail = [history[-1]] if history else []
        history.append({"role": "user", "content": user_turn})
        tail.append(history[-1])
        msgs = _messages_for(case, _sendable_history(history))
        est = _turn_prompt_estimate(msgs, prev, tail) if chain else _prompt_tokens(msgs)
        exact = row_budget(ctx, case, est)
        if chain and ctx.ctx_len:
            room = int(ctx.ctx_len) - est - BANK_WINDOW_SLACK
            if room < CONTEXT_MIN_TURN:
                history.pop()
                rows.append(_context_row(base_row, i, user_turn, "not sent: the window is full",
                                         {"prompt_est": est, "ctx": int(ctx.ctx_len)}))
                stopped = True
                break
        try:
            result = ctx.call(msgs, max_tokens=max_tokens, exact_budget=exact,
                              recover=ctx.recover_for(case),
                              **({"regen": ctx.regen_for(case)} if hasattr(ctx, "regen_for") else {}),
                              temperature=temperature, top_p=top_p, seed=seed)
        except (RequestRejected, GenerationRejected) as e:
            if not (chain and is_ctx_short_error(str(e))):
                raise
            history.pop()
            rows.append(_context_row(base_row, i, user_turn, "refused by the server: context",
                                     {"prompt_est": est, "ctx": ctx.ctx_len, "error": str(e)[:300]}))
            stopped = True
            break
        prev = result
        # The content channel is the answer. The reasoning channel is NOT the
        # answer, but on a provider with reasoning replay it IS the model's
        # context: MiniMax documents that the complete assistant message
        # (reasoning_content included) must be passed back or the chain is
        # lost (1/10 recall with it dropped vs 7/10 replayed). It rides in
        # the REPLAYED history only — the judged conversation stays
        # content-only (see _judged_history).
        # A FINISHED reply whose whole answer sits in the reasoning channel
        # is that answer (reply_of — the single-turn policy): it is what the
        # history carries forward, what the judged conversation shows and
        # what the checks read; the row is stamped answered_in_reasoning.
        reply, _from_reasoning = reply_of(result)
        # one builder for the bench and the conformance probe (runner.
        # assistant_turn): the reasoning channel rides on the assistant turn
        # only on a provider with the reasoning replay on.
        es_stamp = _empty_stop_stamp(ctx, result)
        # 4.5 (lead 2026-11-14): a degenerate reply (a visible loop, or an empty reply whose
        # reasoning loops) is not fed back either -- the same contagion path as a cut draft.
        # It is recorded on its row as it happened, stamped degenerate, and the turn goes into
        # history empty (so the session checks read an empty reply: a failure for that turn).
        # Never regenerated (call(): the degenerate test comes first); the session goes on.
        deg = None
        if getattr(ctx, "empty_stop_rule", False):
            # read as the persisted row will be: an empty stop is an empty answer, so a loop
            # there is in the reasoning channel (api.row_answer)
            try:
                deg = degenerate.row_degenerate({"response": result.response_text or "",
                                                 "reasoning": result.reasoning_text or "",
                                                 "finish_reason": result.finish_reason,
                                                 "tool_calls": result.tool_calls,
                                                 **({"empty_stop": es_stamp} if es_stamp else {})})
            except Exception:  # noqa: BLE001 - the test never costs a row
                deg = None
        if es_stamp is not None or deg:
            # 4.5 empty_stop_rule: a cut draft is not the model's answer; the turn goes
            # into history EMPTY (the recorded history keeps it, _sendable_history
            # leaves it out and joins the user turns either side -- the blank rule),
            # with no reasoning replayed. The session goes on: the cause may be the
            # server, and ending it would score every later turn 0 for that.
            turn = {"role": "assistant", "content": ""}
        else:
            turn = assistant_turn(result, bool(getattr(ctx, "reasoning_replay", False)))
        history.append(turn)
        row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": i,
               "prompt": user_turn, "response": result.response_text,
               "reasoning": result.reasoning_text, "tool_calls": [],
               "finish_reason": result.finish_reason,
               "truncated": result.finish_reason == "length",
               # what was actually sent (boosted for a thinking model)
               "max_tokens_sent": exact or ctx.budget(max_tokens),
               # same stamp as a single-turn row
               "answered_in_reasoning": in_reasoning_flag(result.response_text,
                                                          result.reasoning_text,
                                                          result.tool_calls),
               "metrics": _metrics(result)}
        # 4.5 (EXP-20261114-11 bug 4): what this request sent back of earlier
        # reasoning (index, size, sha256 per message; the turn rows hold the
        # text) -- the stored conversation is content-only
        sent = replayed_digest(msgs)
        if sent:
            row["replayed_reasoning"] = sent
        if es_stamp is not None:
            row["empty_stop"] = {**es_stamp, "history": "empty"}
        if deg:
            row["degenerate"] = {**deg, "history": "empty"}
        _annotate_recovery(row, result)
        # rpdeep: could this turn's prompt + requested budget exceed the context
        # the model was loaded at (possible), and did the turn actually die at
        # the wall (finish=length with no content)? Both are stamped so an
        # overflow is never silently scored as a model miss (review H3/L1).
        pt = result.prompt_tokens
        if pt and ctx.ctx_len and pt + row["max_tokens_sent"] > int(ctx.ctx_len):
            row["context_overflow_possible"] = {"prompt_tokens": pt,
                                                "budget": row["max_tokens_sent"],
                                                "ctx": int(ctx.ctx_len)}
            if result.finish_reason == "length" and not (reply or "").strip():
                row["context_overflow_actual"] = True
        if chain and result.finish_reason == "length" and (
                (ceiling and exact is not None and exact < ceiling) or row_ctx_short(row, ctx.ctx_len)):
            # cut by the window, not by the ceiling: the context ran out (never "ran out")
            row["context_exhausted"] = {"why": "cut: the window clamped the turn below its ceiling",
                                        "prompt_est": est, "budget": exact, "ceiling": ceiling,
                                        "ctx": ctx.ctx_len}
            stopped = i < n_turns
        if i == n_turns or stopped:
            _finish(row)
        rows.append(row)
        if stopped:
            break
    if stopped and rows and not rows[-1].get("needs_judge"):
        _finish(rows[-1])
    return rows


def _assistant_tool_turn(result: ChatResult, calls: list[dict]) -> tuple[dict, list[str]]:
    """The assistant message that carries ``calls`` back into the context,
    and the call ids used. The reasoning channel rides along as
    ``reasoning_content``: DeepSeek's thinking mode REJECTS (HTTP 400 "The
    reasoning_content in the thinking mode must be passed back") a tool-call
    turn without it, and llama.cpp templates that interleave thinking with
    tool calls render it the same way."""
    ids = [(c or {}).get("id") or f"call_{i}" for i, c in enumerate(calls or [{}])]
    msg = {"role": "assistant", "content": result.response_text or "",
           "tool_calls": [{"id": cid, "type": "function",
                           # an empty name (a model's malformed call) is replayed under a placeholder:
                           # Anthropic rejects the whole turn otherwise (HTTP 400 "function.name: String
                           # should have at least 1 character"). Grading reads the original call.
                           "function": {"name": (c or {}).get("name") or "unnamed_tool",
                                        "arguments": (c or {}).get("arguments") or "{}"}}
                          for cid, c in zip(ids, calls or [{}])]}
    if result.reasoning_text:
        msg["reasoning_content"] = result.reasoning_text
    return msg, ids


def _parallel_block(script: list[dict], i: int, calls: list[dict]) -> list[int] | None:
    """Step indices answered by ``calls`` emitted in ONE turn at step ``i``, or
    None when the calls are not a legitimate parallel batch.

    Legitimate = more than one call, distinct tool names, and exactly the
    names of the next ``len(calls)`` consecutive tool steps starting at ``i``
    (no user turn in between). TZ01 step 3: 14 models called get_run_target
    and get_run_duration together — independent lookups on the same run id —
    and gave the exact right final line, yet failed "2 tool calls, expected
    1". Duplicate names are never a batch (TZ07's retry must come AFTER the
    rate-limit result)."""
    n = len(calls)
    if n < 2 or i + n > len(script):
        return None
    names = [c.get("name") for c in calls]
    if len(set(names)) != n:
        return None
    block = list(range(i, i + n))
    for k in block:
        st = script[k]
        if not st.get("expect_tool") or (k > i and st.get("user") is not None):
            return None
    if sorted(script[k]["expect_tool"] for k in block) != sorted(names):
        return None
    return block


def _step_type(step: dict) -> str:
    """Element type of a tool_script step, for failures.md."""
    if step.get("expect_tool"):
        return "tool_call"
    return "no_tool" if step.get("expect_no_tool") else "answer"


#: The tool result fed back when the model called a tool at a step the case
#: wants answered in prose (replay providers only). The step still fails —
#: the case wanted no tool — but the conversation stays well-formed: the
#: call stays on the assistant turn and every call is answered, or the
#: model's unanswered request leaks into the next turn and is re-issued
#: there (which then fails "N tool calls, expected 1").
TOOLS_UNAVAILABLE = '{"error": "not executed: tools are not available at this step"}'


def keeps_reasoning(entry) -> bool:
    """Append-only agentic history (prompt caching) for METERED models, i.e.
    a registry row with a ``price`` block. Free rig/local rows keep the
    historical per-turn reasoning drop so their 4.1 scores are unchanged."""
    return bool((entry or {}).get("price"))


def compaction_mode(ctx) -> str | None:
    """The agentic history rule of the profile this run is for (``compaction``
    key; bench45 = ``hysteresis``, see codeenv.compact). Absent = the 4.1 rule,
    so bench requests stay byte-identical."""
    from .profiles import active_name, profile_switch
    v = profile_switch(active_name(getattr(ctx, "cfg", None)), "compaction")
    return str(v).strip().lower() if v else None


def _run_env_loop(case, base_row, ctx: _Ctx, max_tokens, temperature, seed, top_p=None) -> dict:
    """Code bank agent family: the model works on a project through tools
    that really execute (codeenv.RepoEnv); the final tree is graded by the
    hidden tests plus a scope element. Every turn's budget is the case
    budget; the row is marked truncated (finish=length) when ANY turn ran
    out, so the escalation round sees it."""
    from . import codeenv
    envcfg = case["env"]
    tier = envcfg.get("tier", "standard")
    _cap, stub_after, ctx_cap, max_calls = codeenv.TIERS[tier]
    env = codeenv.RepoEnv(envcfg, tier)
    mode = compaction_mode(ctx)
    system = case["system"]
    if mode == "hysteresis":
        system = f"{system}\n{codeenv.HYSTERESIS_NOTE}"     # the rule is model-visible
    messages: list[dict] = [{"role": "system", "content": system},
                            {"role": "user", "content": case["prompt"]}]
    calls_made, turns, truncated_turns, last = 0, 0, 0, None
    every: list[ChatResult] = []
    turn_budgets: list[int] = []
    end_reason = "answered without a tool call"
    while True:
        check_abort()
        codeenv.compact(messages, stub_after, ctx_cap,
                        keep_reasoning=keeps_reasoning(getattr(ctx, "entry", None)), mode=mode)
        # the task's own conversation cap (same for every model): the visible
        # conversation plus the tool schemas
        if codeenv.estimate_tokens(messages) + codeenv.SCHEMA_TOKENS > ctx_cap:
            end_reason = "context cap"
            break
        if turns >= 2 * max_calls:
            end_reason = "turn limit"
            break
        # what the SERVER must fit: visible + replayed reasoning + schemas +
        # this turn's budget. With the bank budget the turn gets what is left
        # of the window (min MIN_TURN_BUDGET); otherwise the replay is dropped
        # first. If it still cannot fit, the episode ends and the tree is
        # graded (a server 400 must never make the row "errored" = excluded).
        exact = None
        need = codeenv.estimate_tokens(messages, with_reasoning=True)
        if getattr(ctx, "bank_budget", 0):
            exact = bank_budget(ctx, need)
            if exact < codeenv.MIN_TURN_BUDGET:
                for m in messages:
                    m.pop("reasoning_content", None)
                exact = bank_budget(ctx, codeenv.estimate_tokens(messages, with_reasoning=True))
            if exact < codeenv.MIN_TURN_BUDGET:
                end_reason = "context cap (model window)"
                break
            turn_budgets.append(exact)
        elif getattr(ctx, "ctx_len", None):
            room = int(ctx.ctx_len) - ctx.budget(max_tokens)
            if need > room:
                for m in messages:
                    m.pop("reasoning_content", None)
            if codeenv.estimate_tokens(messages, with_reasoning=True) > room:
                end_reason = "context cap (model window)"
                break
        try:
            result = ctx.call(codeenv.wire(messages), max_tokens=max_tokens, exact_budget=exact,
                              recover=ctx.recover_for(case),
                              temperature=temperature, top_p=top_p, seed=seed, tools=codeenv.TOOLS)
        except RequestRejected as e:
            if e.status not in _CASE_LOCAL_REJECT_STATUSES or _billing_rejection(e):
                raise       # a billing refusal is infra: an errored row, never the model's 0
            # mid-episode 400/422 (typically the context window): the work so
            # far is graded as it stands, like the context cap
            end_reason = f"request rejected mid-episode: {str(e)[:120]}"
            break
        last = result
        every.append(result)
        turns += 1
        if result.finish_reason == "length":
            truncated_turns += 1
        calls = result.tool_calls or _extract_tool_calls_from_text(_answer_view(result).scoreable_text())
        if not calls:
            messages.append({"role": "assistant", "content": reply_of(result)[0]})
            end_reason = ("cut off (budget)" if result.finish_reason == "length"
                          else "answered without a tool call")
            break
        msg, ids = _assistant_tool_turn(result, calls)
        messages.append(msg)
        for call, cid in zip(calls, ids):
            calls_made += 1
            out = (env.call((call or {}).get("name", ""), (call or {}).get("arguments") or "{}")
                   if calls_made <= max_calls else "error: tool-call limit reached")
            messages.append({"role": "tool", "tool_call_id": cid, "content": out})
        if env.finished:
            end_reason = "finish"
            break
        if calls_made >= max_calls:
            end_reason = "tool-call limit"
            break
    v = env.grade()
    any_cut = truncated_turns > 0
    detail = v["detail"]
    if v["grade"] == "fail" and any_cut:
        detail = f"TRUNCATED ({truncated_turns} turn(s) hit the budget); {detail}"
    row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
           "max_tokens_sent": (max(turn_budgets) if turn_budgets else ctx.budget(max_tokens)),
           "turn_budgets": turn_budgets or None,
           "prompt": case["prompt"][:200],
           "response": (last.response_text if last else ""),
           "reasoning": (last.reasoning_text if last else ""),
           "tool_calls": [],
           "finish_reason": "length" if any_cut else (last.finish_reason if last else None),
           "truncated": any_cut,
           "grade": v["grade"], "grade_detail": detail[:2000],
           "elements": {"results": v["results"], "rate": v.get("rate")},
           "score": v["score"], "env": v.get("env"), "tamper": v.get("tamper"),
           "agent": {"turns": turns, "tool_calls": calls_made, "end": end_reason,
                     "truncated_turns": truncated_turns, "decoy_calls": env.decoy_calls,
                     "test_edits": env.test_edits, "denied": env.denied,
                     "files_changed": sorted(set(env.writes)),
                     "est_tokens": codeenv.estimate_tokens(messages)},
           "tool_conversation": [{k: v2 for k, v2 in m.items()
                                  if k != "reasoning_content" and not k.startswith("_")}
                                 for m in messages],
           # the reasoning the final context carried back (sha per message; the
           # text is dropped from tool_conversation for size)
           "replayed_reasoning": replayed_digest(messages),
           "n_turns": turns,
           "metrics": _metrics_sum(every)}
    if mode:
        row["compaction"] = mode     # which history rule measured it (bench45 only)
    if last is not None:
        _annotate_recovery(row, last)
    return row


def _run_console_loop(case, base_row, ctx: _Ctx, max_tokens, temperature, seed, top_p=None) -> dict:
    """Interactive console family (COMPACTOR, FC3): one command per reply, the simulator answers.

    The loop is the ported jx-c probe loop (probe-c/p1c2.py) on the engine's call path: the system line and
    the intro open the conversation, every reply is one cycle of ``Session.step_reply`` and its output is the
    next user turn, until the session ends (halted, crushed, locked out, out of turns) or the model stops
    answering. The row is graded /100 by the simulator (``compactor2.score``): ``score`` is that share,
    ``grade`` is "pass" only for a clean halt of the right press, ``elements`` carry the three ladders
    (floor / climb / edge) so a partial-credit board reads them like test groups. Every turn's budget is the case
    budget; the row is marked truncated when ANY turn ran out, so the escalation round sees it.

    Other console families (2026-11-14, C5 TOOLWORLD): ``console.family`` names the gen family; it
    supplies ``new_session(key)`` and ``console_result(session) -> {score 0-1, elements [{id, pass,
    detail}], detail, grade?}``; ``grade`` defaults to "pass" when the score reaches the family's
    ``PASS_SHARE`` (1.0). COMPACTOR (no ``family`` or ``family: compactor``) is scored as before."""
    from . import gen as _gen
    from .gen import compactor as _compactor
    cfg = case["console"]
    fam_name = cfg.get("family") or "compactor"
    fam = _compactor if fam_name == "compactor" else _gen.get(fam_name)
    if fam is not _compactor and not hasattr(fam, "console_result"):
        raise ValueError(f"console family {fam_name!r} has no console_result()")
    sess = fam.new_session(cfg["key"])
    messages: list[dict] = [{"role": "system", "content": case["system"]},
                            {"role": "user", "content": case["prompt"]}]
    every: list[ChatResult] = []
    replies: list[str] = []
    truncated_turns = 0
    last = None
    end_reason = "session over"
    while sess.state == "running":
        check_abort()
        exact = row_budget(ctx, case, _prompt_tokens(messages))
        result = ctx.call(messages, max_tokens=max_tokens, exact_budget=exact,
                          recover=ctx.recover_for(case),
                          temperature=temperature, top_p=top_p, seed=seed)
        last = result
        every.append(result)
        if result.finish_reason == "length":
            truncated_turns += 1
        reply = result.response_text or ""
        replies.append(reply)
        nxt = sess.step_reply(reply)
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": nxt})
        if not reply.strip() and result.finish_reason != "length":
            end_reason = "empty reply"
            break
    halted = sess.state == "stopped"
    if fam is _compactor:
        sc = _compactor.c2.score(sess)
        score = round(float(sc["score"]) / 100.0, 4)
        elements = [{"id": "floor", "pass": sc["floor"] >= 15, "detail": f"{sc['floor']}/15"},
                    {"id": "climb", "pass": sc["climb"] >= 25, "detail": f"{sc['climb']}/25"},
                    {"id": "edge", "pass": sc["edge"] >= 25, "detail": f"{sc['edge']}/60"}]
        detail = (f"{sc['score']}/100 ({sess.state}, {sess.turn} turns, {sess.strikes} faults; floor {sc['floor']} "
                  f"climb {sc['climb']} edge {sc['edge']})")
        grade_ = "pass" if (halted and sc["score"] >= _compactor.PASS_SCORE) else "fail"
        cblock = {"key": cfg["key"], "state": sess.state, "turns": sess.turn, "faults": sess.strikes,
                  "end": end_reason, "truncated_turns": truncated_turns,
                  "ladders": {k: sc[k] for k in ("floor", "climb", "edge")}}
    else:
        cr = fam.console_result(sess)
        score = round(float(cr["score"]), 4)
        elements = [{"id": str(e["id"]), "pass": bool(e.get("pass")),
                     **({"detail": e["detail"]} if e.get("detail") is not None else {})}
                    for e in cr.get("elements") or []]
        detail = str(cr.get("detail") or f"{score:.2f}")
        grade_ = cr.get("grade") or ("pass" if score >= float(getattr(fam, "PASS_SHARE", 1.0)) - 1e-9
                                     else "fail")
        cblock = {"family": fam_name, "key": cfg["key"], "state": sess.state, "turns": sess.turn,
                  "faults": getattr(sess, "strikes", None), "end": getattr(sess, "end", None) or end_reason,
                  "truncated_turns": truncated_turns}
    if truncated_turns and not halted:
        detail = f"TRUNCATED ({truncated_turns} turn(s) hit the budget); {detail}"
    row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
           "max_tokens_sent": ctx.budget(max_tokens),
           "prompt": case["prompt"][:200],
           "response": (last.response_text if last else ""),
           "reasoning": (last.reasoning_text if last else ""),
           "tool_calls": [],
           "finish_reason": "length" if (truncated_turns and not halted) else (last.finish_reason if last else None),
           "truncated": bool(truncated_turns and not halted),
           "grade": grade_,
           "grade_detail": detail[:2000],
           "elements": {"results": elements, "rate": score},
           "score": score,
           "console": cblock,
           "tool_conversation": [m for m in messages],
           "n_turns": sess.turn,
           "metrics": _metrics_sum(every)}
    if last is not None:
        _annotate_recovery(row, last)
    return row


def _run_tool_loop(case, base_row, ctx: _Ctx, max_tokens, temperature, seed, top_p=None) -> dict:
    """Agent tool loop: model calls a tool, we inject a canned tool result,
    the model must incorporate it into a final answer. Grades every step
    (right call, then answer uses the returned data). Independent calls the
    model issues together in one turn are answered together (see
    ``_parallel_block``)."""
    script = case["tool_script"]
    tools = case.get("tools")
    messages: list[dict] = []
    if case.get("system"):
        messages.append({"role": "system", "content": case["system"]})
    unsent_calls: dict[int, list[dict]] = {}     # message index -> a call made but not fed back (record only)
    # the tool revision (lead 2026-11-15 08:27): on a profile with ``tool_call_feedback: all`` a call at a
    # prose step is fed back with TOOLS_UNAVAILABLE for EVERY provider (before: only reasoning-replay ones),
    # and each step's calls / reply are kept on its element (tooltask.task_done reads them)
    feedback_all = _tool_call_feedback(base_row) == "all"

    steps: list[dict | None] = [None] * len(script)
    last_result = None
    every: list[ChatResult] = []
    capped = False
    i = 0
    while i < len(script):
        check_abort()
        step = script[i]
        if step.get("user") is not None:
            messages.append({"role": "user", "content": step["user"]})
        exact = row_budget(ctx, case, _prompt_tokens(messages))
        # the context cap (lead 2026-11-15, D's 8B TZ19 rows): every model is
        # served the same window; a conversation the model's own replies grew
        # past it ends the run here, graded as it stands (a failed run, never
        # an errored/excluded row and never "ran out"), like the codeenv loop
        if not _tool_turn_fits(ctx, messages, tools, exact, max_tokens):
            capped = True
            break
        try:
            result = ctx.call(list(messages), max_tokens=max_tokens, exact_budget=exact,
                              recover=ctx.recover_for(case),
                              temperature=temperature, top_p=top_p, seed=seed, tools=tools)
        except TransportError as e:
            # the estimate is chars/3.5; a degenerate reply can tokenize denser,
            # so the server's own "does not fit" refusal ends the run the same way
            if isinstance(e, ProtocolRejected) or _billing_rejection(e) or not _context_refusal(e):
                raise
            capped = True
            break
        last_result = result
        every.append(result)
        if step.get("expect_tool"):
            # fall back to a text-emitted call so a thinking model's call that
            # failed the wire grammar still survives the loop
            calls = result.tool_calls or _extract_tool_calls_from_text(
                _answer_view(result).scoreable_text())
            block = _parallel_block(script, i, calls)
            if block:
                by_name = {c.get("name"): c for c in calls}
                ordered = [by_name[script[k]["expect_tool"]] for k in block]
                for k, call in zip(block, ordered):
                    steps[k] = grade_tool_call(
                        [call], "", {"expect_tool": script[k]["expect_tool"],
                                     "required_args": script[k].get("required_args", {}),
                                     "forbid_args": script[k].get("forbid_args", {}),
                                     "max_calls": 1})
                    if feedback_all:
                        steps[k] = {**steps[k], **_step_record([call], "")}
                msg, ids = _assistant_tool_turn(result, ordered)
                messages.append(msg)
                for k, cid in zip(block, ids):
                    messages.append({"role": "tool", "tool_call_id": cid,
                                     "content": script[k]["tool_result"]})
                i += len(block)
                continue
            steps[i] = grade_tool_call(result.tool_calls, _answer_view(result).scoreable_text(),
                                       {"expect_tool": step["expect_tool"],
                                        "required_args": step.get("required_args", {}),
                                        "forbid_args": step.get("forbid_args", {}),
                                        "max_calls": step.get("max_calls", 1)})
            if feedback_all:
                steps[i] = {**steps[i], **_step_record(calls, _answer_view(result).scoreable_text())}
            # feed the model's call(s) + our canned result back into context.
            # Every call needs an answer or the next request is malformed: the
            # step's result goes to the first, an explicit "not executed" to
            # any extra (the step already failed on the extra call).
            msg, ids = _assistant_tool_turn(result, calls)
            messages.append(msg)
            for j, cid in enumerate(ids):
                messages.append({"role": "tool", "tool_call_id": cid,
                                 "content": step["tool_result"] if j == 0 else
                                 '{"error": "not executed: one call per step"}'})
        else:
            text = _answer_view(result).scoreable_text()
            calls = result.tool_calls or _extract_tool_calls_from_text(text)
            # one pure grader for generation AND report time (stepgrade;
            # report._recheck re-grades stored rows when needles change)
            called = bool(step.get("expect_no_tool") and calls)
            verdict = grade_answer_step(step, text, called)
            if step.get("expect_no_tool"):
                verdict["called_tool"] = called
                if called:
                    verdict["calls"] = [{"name": c.get("name"), "arguments": c.get("arguments")}
                                        for c in calls][:5]
            if feedback_all:
                verdict = {**verdict, **_step_record(calls, text, keep_text=True)}
            steps[i] = verdict
            if (getattr(ctx, "reasoning_replay", False) or feedback_all) and calls:
                # The model called a tool where the case wanted prose. The
                # step still FAILS (the case wanted no tool) — but the
                # conversation must stay well-formed: the call stays on the
                # assistant turn and EVERY call is answered, or the model's
                # unanswered request leaks into the next turn and is
                # re-issued there (which then fails "N tool calls, expected
                # 1"). MiniMax documents the full assistant message (tool
                # calls included) as the thing to pass back, and an
                # assistant tool_call with no tool result is a malformed
                # request.
                msg, ids = _assistant_tool_turn(result, calls)
                messages.append(msg)
                for cid in ids:
                    messages.append({"role": "tool", "tool_call_id": cid,
                                     "content": TOOLS_UNAVAILABLE})
            else:
                # the step was graded on scoreable_text (reasoning fallback
                # for a finished reply); the conversation carries the same
                # answer forward (assistant_turn / reply_of), and the stored
                # tool_conversation keeps it for stepgrade's re-grade
                if calls:
                    # a call where the case wanted prose, not fed back (no
                    # reasoning replay): the request stays as it was, but the
                    # stored record says what the model did (scrub 2026-11-15:
                    # TZ16 step 4 read as an empty reply, the call only in the
                    # grade's elements)
                    unsent_calls[len(messages)] = [{"name": c.get("name"), "arguments": c.get("arguments")}
                                                   for c in calls]
                messages.append(assistant_turn(result, bool(getattr(ctx, "reasoning_replay",
                                                                    False))))
        i += 1

    if capped:
        # every step never reached counts as wrong: the step share is over ALL
        # the case's steps, and the run cannot pass
        for k in range(len(script)):
            if steps[k] is None:
                steps[k] = {"grade": "fail", "detail": TOOL_CAP_NOT_REACHED, "not_reached": True}
    graded = [s for s in steps if s is not None]
    passed = bool(graded) and all(s["grade"] == "pass" for s in graded) and not capped
    detail = "; ".join(f"step{k+1}:{s['grade']}({s['detail']})"
                       for k, s in enumerate(steps) if s is not None)
    # one step = one element (BFCL multi-turn / tau-bench milestones): the
    # case passes only when every step does, the elements keep each verdict
    results = [{"id": f"step{k + 1}", "type": _step_type(script[k]),
                "pass": s["grade"] == "pass", "detail": str(s["detail"])[:1000],
                **({"called_tool": s["called_tool"]} if "called_tool" in s else {}),
                # validation item 8: the call made at a no-tool step, auditable
                **({"calls": s["calls"]} if s.get("calls") else {}),
                **({"not_reached": True} if s.get("not_reached") else {}),
                # the tool revision: every step's calls and whether it wrote a reply (tooltask)
                **({k2: s[k2] for k2 in ("said", "text") if k2 in s}),
                **({"calls": s["step_calls"]} if "step_calls" in s else {})}
               for k, s in enumerate(steps) if s is not None]
    if capped:
        detail = f"CONTEXT CAP (the conversation no longer fit the served window); {detail}"
    rate = round(sum(r["pass"] for r in results) / len(results), 3) if results else None
    row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
            # every step went out on the boosted budget, like a single row
            "max_tokens_sent": (row_budget(ctx, case, 0) or ctx.budget(max_tokens)),
            "prompt": case["tool_script"][0].get("user", ""),
            "response": (last_result.response_text if last_result else ""),
            "reasoning": (last_result.reasoning_text if last_result else ""),
            "tool_calls": [],
            # a capped run is not a reply cut at its ceiling: its finish is the cap
            "finish_reason": (TOOL_CAP if capped else
                              (last_result.finish_reason if last_result else None)),
            "truncated": False,
            "answered_in_reasoning": (in_reasoning_flag(last_result.response_text,
                                                        last_result.reasoning_text,
                                                        last_result.tool_calls)
                                      if last_result else False),
            "grade": "pass" if passed else "fail", "grade_detail": detail[:2000],
            "elements": {"results": results, "rate": rate},
            # every step's reply and call, so a step verdict in failures.md
            # can be checked against what the model actually said (only the
            # LAST reply used to be stored — step 10's "forbidden content"
            # was unverifiable). The reasoning channel is dropped for size.
            "tool_conversation": [{**{k: v for k, v in m.items() if k != "reasoning_content"},
                                   **({"tool_calls_not_sent": unsent_calls[j]} if j in unsent_calls else {})}
                                  for j, m in enumerate(messages)],
            "replayed_reasoning": replayed_digest(messages),
            "n_turns": len(every),
            "metrics": _metrics_sum(every)}
    if feedback_all:
        row["tool_call_feedback"] = "all"
    if capped:
        row["end_reason"] = TOOL_CAP
    if last_result is not None:
        _annotate_recovery(row, last_result)
    return row


def _tool_call_feedback(base_row: dict):
    """The profile's tool revision switch (``tool_call_feedback``) for the row being made."""
    from . import profiles as _profiles
    return _profiles.profile_switch(base_row.get("profile") or _profiles.active_name(), "tool_call_feedback")


def _step_record(calls, text: str, keep_text: bool = False) -> dict:
    """What a step kept under the tool revision: every call (name + arguments), whether a reply was written,
    and (a prose step that made a call) the reply itself, so the task-done rule can read it."""
    from .api import strip_thinking_tags
    said = bool(strip_thinking_tags(text or "").strip())
    out = {"step_calls": [{"name": c.get("name"), "arguments": c.get("arguments")} for c in calls or []],
           "said": said}
    if keep_text and calls and said:
        out["text"] = strip_thinking_tags(text).strip()[:2000]
    return out


#: the tool loop's recorded end when the conversation outgrew the served window
TOOL_CAP = "context cap"
TOOL_CAP_NOT_REACHED = "not reached (context cap)"
_FIT_REFUSAL_RE = re.compile(r"does not fit the context", re.I)


def _context_refusal(e: Exception) -> bool:
    """A server refusal saying the request did not fit the context window."""
    text = f"{e} {getattr(e, 'body', '') or ''}"
    return bool(_FIT_REFUSAL_RE.search(text)) or is_ctx_short_error(text)


def _tool_turn_fits(ctx, messages: list[dict], tools, exact, max_tokens) -> bool:
    """Whether the next tool-loop request fits the served window: the visible
    conversation (+ any replayed reasoning) + the tool schemas + this turn's
    budget, within the window less the template slack. A replayed reasoning
    channel is dropped first (as in the codeenv loop). No window known = fits."""
    if not getattr(ctx, "ctx_len", None):
        return True
    schema = int(len(json.dumps(tools or [])) / 3.5)
    window = int(ctx.ctx_len) - BANK_WINDOW_SLACK
    # the server refuses only a PROMPT that does not fit; the turn must still
    # have a working floor to answer in (the codeenv loop's MIN_TURN_BUDGET),
    # never more than the budget it is actually sent
    from .codeenv import MIN_TURN_BUDGET
    need_out = min(MIN_TURN_BUDGET, int(exact or ctx.budget(max_tokens)))
    if _prompt_tokens(messages) + schema + need_out <= window:
        return True
    for m in messages:
        m.pop("reasoning_content", None)
    return _prompt_tokens(messages) + schema + need_out <= window
