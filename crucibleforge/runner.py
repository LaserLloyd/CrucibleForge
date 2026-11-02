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

import csv
from pathlib import Path
import json
import logging
import re
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as _fwait
from datetime import datetime, timezone

from . import lms, session_checks, stopflag, studioforge
from dataclasses import replace as _dc_replace

from .api import (Aborted, ChatResult, GenerationRejected, RequestRejected, RowTimeout,
                  TransportError, WindowClosed, WrongModelError)
from .config import CSV_COLUMNS, results_dir, append_transcript, repeats_for
from .graders import (_extract_tool_calls_from_text, grade, grade_tool_call, prose_metrics)
from .providers import Provider, cost_usd, merge_extra_body, model_extra_body, provider_for
from .version import judge_fingerprint as _judge_fp, revision as _revision
from .version import private_category_hash
from .stepgrade import grade_answer_step
from . import config as _config

log = logging.getLogger(__name__)

CREATIVE_SEEDS = [41, 42, 43, 44, 45]
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


class ModelRunError(RuntimeError):
    pass


class RunStopped(RuntimeError):
    pass


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
    return {"ttft_s": results[0].ttft_s, "gen_s": gen, "total_s": tot("total_s"),
            "prompt_tokens": tot("prompt_tokens"), "completion_tokens": comp,
            "reasoning_tokens": tot("reasoning_tokens"),
            "tok_per_s": (round(comp / gen, 2) if gen and comp and comp >= 2 else None),
            "prompt_tok_per_s": results[-1].prompt_tok_per_s,
            "n_turns": len(results)}


def _metrics(result: ChatResult) -> dict:
    return {
        "ttft_s": result.ttft_s, "gen_s": result.gen_s, "total_s": result.total_s,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "reasoning_tokens": result.reasoning_tokens,
        "tok_per_s": result.tok_per_s, "prompt_tok_per_s": result.prompt_tok_per_s,
    }


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


class _Ctx:
    """Everything a single model run needs, shared by the case workers."""

    def __init__(self, cfg, entry, provider: Provider, ctx_len):
        self.cfg = cfg
        self.entry = entry
        self.provider = provider
        self.model_id = entry["model_id"]
        self.ctx_len = ctx_len
        self.extra_body = model_extra_body(entry)
        # Provider-gated reasoning replay (MiniMax's documented protocol: the
        # assistant turn goes back WITH its reasoning). OFF by default -- a
        # provider without it sends byte-identical requests to before.
        self.reasoning_replay = bool(getattr(provider, "reasoning_replay", False))
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

    def recover_for(self, case: dict) -> bool:
        """Whether reasoning-overflow recovery applies to this case."""
        return case["category"] != "perf" and case["category"] not in self.no_recovery

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
                if self.regime == REGIME_DEPLOYED else "case"}

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
        try:
            r = self.provider.chat(self.model_id, messages, **kw)
        except WrongModelError as e:
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
        text is never taken as the answer: nothing distinguishes a reply
        from real chain-of-thought (deepseek's starts "We need answer as…")."""
        return r.finish_reason == "stop" and not cls._answered(r)

    def call(self, messages, *, max_tokens: int, recover: bool = True, **kw) -> ChatResult:
        """One completion on the CASE budget ``max_tokens`` (boosted for a
        thinking model), with recovery for a reply that never reached the
        content channel (reasoning overflow, or a stop inside the reasoning
        block). ``recover=False`` for timing rows (perf), whose metrics must
        stay single-shot."""
        kw.setdefault("extra_body", self.extra_body)
        exact = kw.pop("exact_budget", None)
        first = self._chat(messages, max_tokens=exact or self.budget(max_tokens), **kw)
        # (under a profile with the old thinking-off recovery, that recovery
        # handles a stop without an answer instead)
        if recover and not self.recovery_enabled and _empty_stop(first):
            # 4.0 (validation item 10): a reply that comes back EMPTY with
            # finish=stop — no content, no tool call — is a
            # serving hiccup, not an answer: the same request is sent ONCE
            # more (budget, sampler and thinking unchanged). A second empty
            # reply stands.
            log.warning("%s: empty reply with finish=stop — retrying once", self.model_id)
            again = self._chat(messages, max_tokens=exact or self.budget(max_tokens), **kw)
            again.empty_retry = True
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
        final.total_s = sum(a.total_s or 0.0 for a in attempts)


def run_models(cfg: dict, model_entries: list[dict], cases: list[dict],
               smoke: bool = False, jobs_by_label: dict | None = None,
               rung: int | None = None, min_ctx: int | None = None,
               record_key: str | None = None) -> dict:
    """jobs_by_label: optional {label: {(bench_run_id, case_id, repeat), ...}}
    restricting each model to exactly those (case, repeat) jobs, re-run under
    their ORIGINAL bench_run_id so the new rows supersede the old ones (used
    by ``crucibleforge recover``). ``rung``: an escalation round — the jobs
    (bench_run_id None) run on that output budget under a NEW bench_run_id and
    their rows carry ``budget_rung`` (see DEFAULT_ESCALATION_RUNGS).
    ``min_ctx``: load each model at least at this context (a tier-3 round,
    tiers.py). ``record_key``: the meta list a jobs round is recorded under
    (default "escalated" for a rung, else "recovered"; a tier round: "tiers")."""
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
            label = entry["name"]
            if not (jobs_by_label and label in jobs_by_label):
                with _ACTIVE_LOCK:
                    ACTIVE[label] = (entry, provider_for(cfg, entry), cfg)
            try:
                rounds = plan_rounds(cfg, entry, cases, (jobs_by_label or {}).get(label),
                                     rung=rung, min_ctx=min_ctx)
                if len(rounds) > 1:
                    log.info("%s: %s", label, "; ".join(
                        f"round {r['name']} at ctx {r['ctx']}" for r in rounds))
                rid = str(uuid.uuid4())[:8]
                carry: dict = {}
                parts = []
                for i, rd in enumerate(rounds):
                    parts.append(_run_one_model(
                        cfg, entry, rd["cases"], csvw, smoke, only_jobs=rd["only_jobs"],
                        rung=rung, min_ctx=min_ctx, record_key=record_key,
                        reset=(i == 0), bench_run_id=rid, perf=(i == 0),
                        last=(i == len(rounds) - 1),
                        round_name=rd["name"] if len(rounds) > 1 else None, carry=carry))
                summary[label] = _merge_round_summaries(parts)
            except RunStopped:
                _record_stop(cfg, entry, label, summary, jobs_by_label, record_key, rung)
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
                if jobs_by_label and label in jobs_by_label:
                    _write_recover_record(label, failed=True, error=str(e)[:500],
                                          key=record_key or ("escalated" if rung else "recovered"), rung=rung)
                else:
                    _write_meta(label, entry, provider_for(cfg, entry), failed=True,
                                error=str(e)[:2000], profile=cfg.get("_profile"),
                                revision=_revision(cfg))
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
    if len(parts) > 1:
        out["rounds"] = [{"round": p.get("round"), "ctx": p.get("ctx"), "jobs": p.get("jobs"),
                          "workers": p.get("workers"), "plan": p.get("plan")} for p in parts]
    return out


def _record_stop(cfg, entry, label, summary, jobs_by_label, record_key, rung) -> None:
    """Summary + meta for a model whose run was stopped: a hard stop marks the
    meta ``stopped`` (with a finished time), a cooperative one as before."""
    hard = ABORT.is_set()
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
    error = "stopped (SIGTERM)" if hard else "stopped by user"
    summary[label] = {"failed": True, "error": error, "stopped": True}
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
    ctx_len = entry.get("context_length") or cfg["defaults"].get("context_length")
    for floor in (cfg["defaults"].get("context_floor"), min_ctx, bank_context(cfg, cases),
                  wide_context(cfg, cases)):
        if floor and ctx_len:
            ctx_len = max(int(ctx_len), int(floor))
    if rung:
        # the answer must fit: prompt + a rung-sized completion
        ctx_len = max(int(ctx_len or 0), int(rung) + ESCALATION_CTX_PAD)
    return ctx_len


def _run_one_model(cfg, entry, cases, csvw: _Csv, smoke,
                   only_jobs: set | None = None, rung: int | None = None,
                   min_ctx: int | None = None, record_key: str | None = None,
                   reset: bool = True, bench_run_id: str | None = None, perf: bool = True,
                   last: bool = True, round_name: str | None = None,
                   carry: dict | None = None) -> dict:
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
    if not recover_mode and reset:
        _write_meta(label, entry, provider, load_s=load_s, bench_run_id=bench_run_id,
                    profile=cfg.get("_profile"), plan=plan or None, ctx_len=ctx_len,
                    revision=_revision(cfg), reset=True, started=t_start)
    ctx.adopt_rig_reasoning()
    ctx.detect_thinking()

    state = {"rows": 0, "sanity_total": 0, "sanity_empty": 0,
             "speed_samples": [], "speed_gated": False, "skipped": 0,
             "cost": 0.0, "consecutive_errors": 0, "case_errors": 0}
    min_tps = float(cfg["defaults"].get("min_tok_per_s", 0) or 0)
    revision = _revision(cfg)
    judge_fp = _judge_fp(cfg)

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
            for row in rows:
                c = cost_usd(entry, (row.get("metrics") or {}).get("prompt_tokens"),
                             (row.get("metrics") or {}).get("completion_tokens"))
                if c is not None:
                    row["cost_usd"] = round(c, 6)
                    state["cost"] += c
                append_transcript(label, row)
                csvw.write({**row, "run_id": row["run_id"],
                            "grade": row.get("grade"),
                            "grade_detail": row.get("grade_detail"),
                            **(row.get("metrics") or {})})
                state["rows"] += 1
            # sanity: if the first N completions are all empty, the model is
            # misconfigured — stop burning hours on it
            for row in rows:
                if row.get("skipped") or row.get("error"):
                    continue  # not a completion — counted by the transport guard
                if row.get("turn") in (None, 1):
                    state["sanity_total"] += 1
                    if not (row.get("response") or row.get("reasoning") or row.get("tool_calls")):
                        state["sanity_empty"] += 1
            if (state["sanity_total"] >= SANITY_CHECK_AFTER
                    and state["sanity_empty"] == state["sanity_total"]):
                raise ModelRunError(
                    f"first {state['sanity_total']} completions all empty for {label}")
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
        if STOP.is_set():
            raise RunStopped()
        check_abort()
        if abort.is_set():
            return
        temperature, top_p = ctx.sampling(case)
        max_tokens = case["max_tokens"]
        # a sampled row (any temperature but 0) gets a seed per repeat, so
        # repeats are independent draws
        seed = 42 if temperature == 0 else CREATIVE_SEEDS[(repeat - 1) % len(CREATIVE_SEEDS)]
        base_row = base_row_for(case, repeat, seed, temperature, top_p, max_tokens, rid)

        need_ctx = int(case.get("min_context") or 0)
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

        if not provider.is_loaded(model_id):
            log.warning("%s evicted — reloading", model_id)
            check_abort()
            # not under ctx.lock: a reload waits up to wait_ready's 900 s and
            # every other worker's persist() needs ctx.lock (M10)
            with ctx.reload_lock:
                if not provider.is_loaded(model_id):
                    provider.switch_model(model_id, ctx_len)

        try:
            if case.get("env"):
                rows = [_run_env_loop(case, base_row, ctx, max_tokens, temperature, seed, top_p=top_p)]
            elif case.get("tool_script"):
                rows = [_run_tool_loop(case, base_row, ctx, max_tokens, temperature, seed, top_p=top_p)]
            elif case.get("turns"):
                rows = _run_multiturn(case, base_row, ctx, max_tokens, temperature, top_p, seed)
            else:
                rows = [_run_single(case, base_row, ctx, max_tokens, temperature, top_p, seed)]
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
                kind = "generation" if case.get("category") in BANK_CATEGORIES else "transport"
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
        persist(rows)
        log.info("[%s] %s r%d done (%d rows)", label, case["id"], repeat, state["rows"])

    # perf first, serially, with a warmup so the first TTFT isn't cold-start
    perf_cases = ([c for c in cases if c["category"] == "perf"]
                  if not recover_mode and perf else [])
    other_cases = [c for c in cases if c["category"] != "perf"]
    if perf_cases:
        t0, p0 = ctx.sampling({})
        ctx.call([{"role": "user", "content": "Hi"}], max_tokens=8,
                 temperature=t0, top_p=p0, seed=42, recover=False)
        # serial (one worker), but on a pool the main thread can leave on ABORT
        _run_pool([(run_case_repeat, (case, repeat, None), (case, repeat))
                   for case in perf_cases
                   for repeat in range(1, repeats_for(cfg, "perf", smoke) + 1)], 1, abort, label)

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
    else:
        jobs = [(c, r, None) for c in other_cases
                for r in range(1, repeats_for(cfg, c["category"], smoke, c) + 1)]
    jobs = schedule_jobs(jobs, _job_seconds_fn(label, other_cases,
                                               getattr(provider, "type", None) in _LOADING_TYPES))
    workers = provider.workers(model_id)
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
    first_err = _run_pool([(run_case_repeat, (c, r, rid), (c, r)) for c, r, rid in jobs],
                          max(1, workers), abort, label)
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
    round_rec = {"round": round_name or "all", "ctx": ctx_len, "jobs": len(jobs),
                 "rows": state["rows"], "workers": workers, "load_s": round(load_s, 1),
                 "s": secs, "plan": plan or None}
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
            "cost_usd": round(state["cost"], 4) if entry.get("price") else None}


def _run_pool(calls: list[tuple], workers: int, abort: threading.Event, label: str):
    """Run ``fn(*args)`` for every ``(fn, args, key)`` on ``workers`` threads
    and return the first RunStopped / ModelRunError / transport error (None
    when all finished). A ModelRunError sets ``abort`` (this model's
    remaining jobs drain; the batch goes on), and the queue is not drained
    for it — every remaining job returns at once.

    The main thread waits in short polls instead of ``as_completed`` inside
    ``with ThreadPoolExecutor``: on a hard stop (ABORT) it cancels the queued
    jobs and leaves WITHOUT joining the workers. A worker blocked in a socket
    to an unreachable rig used to keep the ``with`` block (and so the whole
    process) alive until systemd SIGABRTed it (STEP4 defect 1)."""
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


def _empty_stop(r) -> bool:
    """A completion that finished normally (finish=stop) with no answer and no
    tool call — reasoning may be present (a model that stopped inside its
    thinking); an overflow (finish=length) is NOT this: it counts as ran out."""
    return (getattr(r, "finish_reason", None) == "stop"
            and not (getattr(r, "response_text", "") or "").strip()
            and not getattr(r, "tool_calls", None))


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
    if kind in ("transport", "template"):
        row["grade"] = "error"
        row["grade_detail"] = error[:300]
        return row
    if case.get("grader") or case.get("tool_script") or case.get("env"):
        row["grade"] = "fail"
        row["grade_detail"] = error[:300]
        if case.get("grader") == "repo_tests" or case.get("env"):
            row["score"] = 0.0
    if case.get("rubric") or case.get("turns"):
        # judged case: nothing to judge — recorded as an empty generation
        row["needs_judge"] = True
        row["rubric"] = case.get("rubric") or ("rp_multi" if case.get("turns") else None)
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
        if "stopped by user" in str(rec.get("error") or ""):
            continue                      # a STOP / window edge is not the rung's fault
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
            return report.board_filter(label, cfg, with_rungs=True)
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
           "answered_in_reasoning": (not result.response_text.strip()
                                     and not result.tool_calls
                                     and bool(result.reasoning_text.strip())),
           "metrics": _metrics(result)}
    _annotate_recovery(row, result)
    verdict = grade(_answer_view(result), case)
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
    # deterministic identity / continuity / constraint checks (session_checks)
    chk = session_checks.run_checks(case, [_answer_view(result).response_text])
    if chk:
        row["checks"] = chk
    # objective prose metrics for creative writing (de-loads the judge)
    if case["category"] in CREATIVE_CATEGORIES and result.response_text.strip():
        row["prose"] = prose_metrics(result.response_text)
    return row


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


def _judged_history(history: list[dict]) -> list[dict]:
    """The recorded history as the JUDGE reads it: content only. The
    reasoning channel rides in the REPLAYED history (it is the model's own
    context, not its answer) and never in the judged conversation."""
    if not any("reasoning_content" in m for m in history):
        return history
    return [{k: v for k, v in m.items() if k != "reasoning_content"} for m in history]


def _run_multiturn(case, base_row, ctx: _Ctx, max_tokens, temperature, top_p, seed) -> list[dict]:
    """Drive scripted user turns; per-turn rows carry metrics, the final row
    carries the whole conversation and goes to the judge."""
    rows: list[dict] = []
    history: list[dict] = []
    n_turns = len(case["turns"])
    for i, user_turn in enumerate(case["turns"], start=1):
        check_abort()   # a hard stop ends a session between turns, never mid-turn
        history.append({"role": "user", "content": user_turn})
        msgs = _messages_for(case, _sendable_history(history))
        exact = row_budget(ctx, case, _prompt_tokens(msgs))
        result = ctx.call(msgs, max_tokens=max_tokens, exact_budget=exact,
                          recover=ctx.recover_for(case),
                          temperature=temperature, top_p=top_p, seed=seed)
        # The content channel is the answer. The reasoning channel is NOT the
        # answer, but on a provider with reasoning replay it IS the model's
        # context: MiniMax documents that the complete assistant message
        # (reasoning_content included) must be passed back or the chain is
        # lost (1/10 recall with it dropped vs 7/10 replayed). It rides in
        # the REPLAYED history only — the judged conversation stays
        # content-only (see _judged_history).
        reply = result.response_text
        turn = {"role": "assistant", "content": reply}
        if getattr(ctx, "reasoning_replay", False) and result.reasoning_text:
            turn["reasoning_content"] = result.reasoning_text
        history.append(turn)
        row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": i,
               "prompt": user_turn, "response": result.response_text,
               "reasoning": result.reasoning_text, "tool_calls": [],
               "finish_reason": result.finish_reason,
               "truncated": result.finish_reason == "length",
               # what was actually sent (boosted for a thinking model)
               "max_tokens_sent": exact or ctx.budget(max_tokens),
               "metrics": _metrics(result)}
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
        if i == n_turns:
            row["needs_judge"] = True
            row["rubric"] = case.get("rubric", "rp_multi")
            row["conversation"] = (
                ([{"role": "system", "content": case["system"]}]
                 if case.get("system") else []) + _judged_history(history))
            if case.get("judge_key"):
                row["judge_key"] = case["judge_key"]
            if case.get("history"):
                row["history_len"] = len(case["history"])
            replies = [m["content"] for m in history if m["role"] == "assistant"]
            chk = session_checks.run_checks(case, replies)
            if chk:
                row["checks"] = chk
            written = "\n\n".join(r for r in replies if r.strip())
            if case["category"] in CREATIVE_CATEGORIES and written:
                row["prose"] = prose_metrics(written)
        rows.append(row)
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
                           "function": {"name": (c or {}).get("name", ""),
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
    messages: list[dict] = [{"role": "system", "content": case["system"]},
                            {"role": "user", "content": case["prompt"]}]
    calls_made, turns, truncated_turns, last = 0, 0, 0, None
    every: list[ChatResult] = []
    turn_budgets: list[int] = []
    end_reason = "answered without a tool call"
    while True:
        check_abort()
        codeenv.compact(messages, stub_after, ctx_cap)
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
            if e.status not in _CASE_LOCAL_REJECT_STATUSES:
                raise
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
            messages.append({"role": "assistant", "content": result.response_text or ""})
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
           "n_turns": turns,
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

    steps: list[dict | None] = [None] * len(script)
    last_result = None
    every: list[ChatResult] = []
    i = 0
    while i < len(script):
        check_abort()
        step = script[i]
        if step.get("user") is not None:
            messages.append({"role": "user", "content": step["user"]})
        exact = row_budget(ctx, case, _prompt_tokens(messages))
        result = ctx.call(list(messages), max_tokens=max_tokens, exact_budget=exact,
                          recover=ctx.recover_for(case),
                          temperature=temperature, top_p=top_p, seed=seed, tools=tools)
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
            steps[i] = verdict
            if getattr(ctx, "reasoning_replay", False) and calls:
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
                turn = {"role": "assistant", "content": result.response_text}
                if getattr(ctx, "reasoning_replay", False) and result.reasoning_text:
                    turn["reasoning_content"] = result.reasoning_text
                messages.append(turn)
        i += 1

    graded = [s for s in steps if s is not None]
    passed = bool(graded) and all(s["grade"] == "pass" for s in graded)
    detail = "; ".join(f"step{k+1}:{s['grade']}({s['detail']})"
                       for k, s in enumerate(steps) if s is not None)
    # one step = one element (BFCL multi-turn / tau-bench milestones): the
    # case passes only when every step does, the elements keep each verdict
    results = [{"id": f"step{k + 1}", "type": _step_type(script[k]),
                "pass": s["grade"] == "pass", "detail": str(s["detail"])[:1000],
                **({"called_tool": s["called_tool"]} if "called_tool" in s else {}),
                # validation item 8: the call made at a no-tool step, auditable
                **({"calls": s["calls"]} if s.get("calls") else {})}
               for k, s in enumerate(steps) if s is not None]
    rate = round(sum(r["pass"] for r in results) / len(results), 3) if results else None
    row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
            # every step went out on the boosted budget, like a single row
            "max_tokens_sent": (row_budget(ctx, case, 0) or ctx.budget(max_tokens)),
            "prompt": case["tool_script"][0].get("user", ""),
            "response": (last_result.response_text if last_result else ""),
            "reasoning": (last_result.reasoning_text if last_result else ""),
            "tool_calls": [], "finish_reason": (last_result.finish_reason if last_result else None),
            "truncated": False,
            "grade": "pass" if passed else "fail", "grade_detail": detail[:2000],
            "elements": {"results": results, "rate": rate},
            # every step's reply and call, so a step verdict in failures.md
            # can be checked against what the model actually said (only the
            # LAST reply used to be stored — step 10's "forbidden content"
            # was unverifiable). The reasoning channel is dropped for size.
            "tool_conversation": [{k: v for k, v in m.items() if k != "reasoning_content"}
                                  for m in messages],
            "n_turns": len(every),
            "metrics": _metrics_sum(every)}
    if last_result is not None:
        _annotate_recovery(row, last_result)
    return row
