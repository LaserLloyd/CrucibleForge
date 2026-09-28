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
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from . import lms, session_checks, studioforge
from dataclasses import replace as _dc_replace

from .api import (ChatResult, GenerationRejected, RequestRejected, TransportError,
                  WrongModelError)
from .config import CSV_COLUMNS, results_dir, append_transcript, repeats_for
from .graders import (_extract_tool_calls_from_text, grade, grade_contains,
                      grade_tool_call, prose_metrics)
from .providers import Provider, cost_usd, merge_extra_body, model_extra_body, provider_for
from .version import judge_fingerprint as _judge_fp, revision as _revision
from .version import private_category_hash
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
RECOVERY_NO_THINK_KWARGS = {"chat_template_kwargs": {"enable_thinking": False}}
RECOVERY_CONTINUE_PROMPT = (
    "Your reasoning above was cut off by the length limit. Do not think "
    "further. Reply now with ONLY your final answer to the original request.")
# how much of the truncated reasoning to feed back on the continuation rung
RECOVERY_REASONING_TAIL_CHARS = 6000

# Cooperative stop: set() to finish the in-flight case(s) and stop cleanly.
STOP = threading.Event()

# Job scheduling order (2026-09-23): multi-turn sessions go first — a 6-turn
# chat session is 6 DEPENDENT generations, the serial long pole of the chat
# half, so it must overlap the long coding rows instead of trailing them —
# then the categories with the longest single generations, so the pool's
# slots are never left idle waiting on one straggling 24k-token coding case
# at the end of the run. Anything not named here runs last, in profile order.
CATEGORY_ORDER = ["coding", "math", "reasoning", "rp", "nsfw", "story", "tooluse", "steer",
                  "instruct"]
# categories whose written replies get objective prose metrics
CREATIVE_CATEGORIES = ("rp", "nsfw", "story")

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


def _metrics(result: ChatResult) -> dict:
    return {
        "ttft_s": result.ttft_s, "gen_s": result.gen_s, "total_s": result.total_s,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "reasoning_tokens": result.reasoning_tokens,
        "tok_per_s": result.tok_per_s, "prompt_tok_per_s": result.prompt_tok_per_s,
    }


class _Ctx:
    """Everything a single model run needs, shared by the case workers."""

    def __init__(self, cfg, entry, provider: Provider, ctx_len):
        self.cfg = cfg
        self.entry = entry
        self.provider = provider
        self.model_id = entry["model_id"]
        self.ctx_len = ctx_len
        self.extra_body = model_extra_body(entry)
        self.lock = threading.Lock()

        # Thinking models spend the completion budget on CoT; a case's
        # max_tokens (sized for a direct answer) then truncates them before
        # any answer appears. entry.thinking: true|false|"auto" (default auto:
        # flips to true the first time a reply carries reasoning tokens).
        t = entry.get("thinking", "auto")
        self.thinking: bool | None = None if t == "auto" else bool(t)
        # a registry entry that configures a reasoning channel IS a thinking
        # model, whatever a trivial probe says
        if self.thinking is None and self.extra_body.get("reasoning_format"):
            self.thinking = True
        d = cfg.get("defaults", {})
        self.think_factor = float(d.get("thinking_max_tokens_factor", 4))
        self.think_cap = int(d.get("thinking_max_tokens_cap", 32768))
        # The profile's cap (24576) exists because the RIG's 32768 context
        # must hold prompt + output. A remote API with a far larger window
        # (DeepSeek / MiniMax: 1M) may override both knobs per provider in
        # models.yaml — "don't limit models" (deepseek-pro lost 4 Programs
        # rows truncated-empty at 24576 reasoning tokens on 2026-09-24).
        pcfg = ((cfg.get("providers") or {}).get(getattr(provider, "name", None)) or {})
        if getattr(provider, "type", None) not in ("studioforge", "lmstudio"):
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

    def recover_for(self, case: dict) -> bool:
        """Whether reasoning-overflow recovery applies to this case."""
        return case["category"] != "perf" and case["category"] not in self.no_recovery

    def budget(self, max_tokens: int) -> int:
        """max_tokens to actually send for this model."""
        if self.thinking:
            return int(min(self.think_cap, max(max_tokens, max_tokens * self.think_factor)))
        return int(max_tokens)

    def _observe(self, result: ChatResult) -> None:
        if self.thinking is None and (result.reasoning_tokens or result.reasoning_text):
            log.info("%s emits reasoning tokens — treating it as a thinking model "
                     "(max_tokens ×%g, cap %d)", self.model_id, self.think_factor, self.think_cap)
            self.thinking = True

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
        try:
            r = self.provider.chat(
                self.model_id,
                [{"role": "user", "content": "What is 17 * 23 + 4? Work it out, then "
                                             "give the number."}],
                max_tokens=256, temperature=0.0, seed=42, extra_body=self.extra_body)
        except (TransportError, WrongModelError) as e:
            log.warning("thinking probe failed (%s) — will auto-detect from replies", e)
            return
        self._observe(r)
        if self.thinking is None:
            log.info("%s: probe showed no reasoning channel — auto-detect stays armed",
                     self.model_id)

    def _chat(self, messages, **kw) -> ChatResult:
        """One completion; on eviction/wrong-model, reload once and retry."""
        if self.stall_s:
            kw.setdefault("stall_s", self.stall_s)
        try:
            r = self.provider.chat(self.model_id, messages, **kw)
        except WrongModelError as e:
            log.warning("%s — reloading and retrying once", e)
            with self.lock:
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
        first = self._chat(messages, max_tokens=self.budget(max_tokens), **kw)
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
               smoke: bool = False, jobs_by_label: dict | None = None) -> dict:
    """jobs_by_label: optional {label: {(bench_run_id, case_id, repeat), ...}}
    restricting each model to exactly those (case, repeat) jobs, re-run under
    their ORIGINAL bench_run_id so the new rows supersede the old ones (used
    by ``crucibleforge recover``)."""
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
            if STOP.is_set():
                log.warning("stop requested — skipping remaining models")
                break
            label = entry["name"]
            try:
                summary[label] = _run_one_model(
                    cfg, entry, cases, csvw, smoke,
                    only_jobs=(jobs_by_label or {}).get(label))
            except RunStopped:
                summary[label] = {"failed": True, "error": "stopped by user"}
                if jobs_by_label and label in jobs_by_label:
                    _write_recover_record(label, failed=True, error="stopped by user")
                else:
                    _write_meta(label, entry, provider_for(cfg, entry), failed=True,
                                error="stopped by user", profile=cfg.get("_profile"),
                                revision=_revision(cfg))
                break
            except (ModelRunError, TransportError, WrongModelError,
                    lms.LmsError, studioforge.StudioForgeError) as e:
                log.error("model %s FAILED: %s — continuing with next model", label, e)
                summary[label] = {"failed": True, "error": str(e)[:2000]}
                if jobs_by_label and label in jobs_by_label:
                    _write_recover_record(label, failed=True, error=str(e)[:500])
                else:
                    _write_meta(label, entry, provider_for(cfg, entry), failed=True,
                                error=str(e)[:2000], profile=cfg.get("_profile"),
                                revision=_revision(cfg))
    finally:
        csvw.close()
    return summary


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
                started: str | None = None) -> None:
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
    if warnings:
        meta["warnings"] = list(warnings)
    if error:
        meta["error"] = error
    if failed:
        meta.pop("finished", None)
    if finished:
        meta["finished"] = _now()
    results_dir().mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _run_one_model(cfg, entry, cases, csvw: _Csv, smoke,
                   only_jobs: set | None = None) -> dict:
    label = entry["name"]
    model_id = entry["model_id"]
    ctx_len = entry.get("context_length") or cfg["defaults"].get("context_length")
    provider = provider_for(cfg, entry)
    bench_run_id = str(uuid.uuid4())[:8]
    ctx = _Ctx(cfg, entry, provider, ctx_len)
    recover_mode = only_jobs is not None
    # a model-local abort (sanity / speed floor / transport storm) must not
    # touch the global STOP, or every later model in the batch is skipped
    abort = threading.Event()

    t_start = _now()
    log.info("=== %s (%s) via %s [%s], ctx=%s ===", label, model_id,
             provider.name, provider.type, ctx_len)
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
    plan = provider.loaded_plan_for(model_id) if hasattr(provider, "loaded_plan_for") else {}
    if not recover_mode:
        _write_meta(label, entry, provider, load_s=load_s, bench_run_id=bench_run_id,
                    profile=cfg.get("_profile"), plan=plan or None, ctx_len=ctx_len,
                    revision=_revision(cfg), reset=True, started=t_start)
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
        return {**extra,
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
        }

    def persist(rows):
        with ctx.lock:
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
        if abort.is_set():
            return
        temperature = case.get("temperature", 0.0)
        top_p = case.get("top_p", 1.0)
        max_tokens = case["max_tokens"]
        seed = CREATIVE_SEEDS[(repeat - 1) % len(CREATIVE_SEEDS)] if temperature > 0 else 42
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
            with ctx.lock:
                provider.switch_model(model_id, ctx_len)

        try:
            if case.get("tool_script"):
                rows = [_run_tool_loop(case, base_row, ctx, max_tokens, temperature, seed)]
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
            if isinstance(e, RequestRejected) and e.status in _CASE_LOCAL_REJECT_STATUSES:
                # a request the server refuses as malformed (HTTP 400/422 —
                # e.g. DeepSeek's thinking mode demanding reasoning_content
                # back) is this case erroring, not the server going away
                log.error("[%s] %s r%d request rejected: %s", label, case["id"], repeat,
                          str(e)[:200])
                with ctx.lock:
                    state["case_errors"] += 1
                persist([_error_row(base_row, case, f"request rejected: {e}", kind="transport")])
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
    perf_cases = [c for c in cases if c["category"] == "perf"] if not recover_mode else []
    other_cases = [c for c in cases if c["category"] != "perf"]
    if perf_cases:
        ctx.call([{"role": "user", "content": "Hi"}], max_tokens=8,
                 temperature=0.0, seed=42, recover=False)
        for case in perf_cases:
            for repeat in range(1, repeats_for(cfg, "perf", smoke) + 1):
                run_case_repeat(case, repeat)

    if recover_mode:
        # job unit = the exact (run id, case, repeat) triple, so the same case
        # overflowing in two accumulated runs is re-run for each of them
        by_id = {c["id"]: c for c in other_cases}
        jobs = [(by_id[cid], rep_, rid) for rid, cid, rep_ in sorted(only_jobs) if cid in by_id]
        missing = sorted(j for j in only_jobs if j[1] not in by_id)
        for j in missing:
            log.warning("recover: %s r%s (run %s) is not in the selected case set — skipped",
                        j[1], j[2], j[0])
        log.info("recover mode: %d job(s) to re-run for %s (%d not selectable)",
                 len(jobs), label, len(missing))
    else:
        jobs = [(c, r, None) for c in other_cases
                for r in range(1, repeats_for(cfg, c["category"], smoke) + 1)]
    jobs = schedule_jobs(jobs)
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
    if workers <= 1:
        for case, repeat, rid in jobs:
            run_case_repeat(case, repeat, rid)
    else:
        log.info("running %d jobs with concurrency=%d", len(jobs), workers)
        first_err = None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(run_case_repeat, c, r, rid): (c, r) for c, r, rid in jobs}
            for fut in as_completed(futs):
                try:
                    fut.result()
                except RunStopped as e:
                    first_err = first_err or e  # STOP is already set by the user
                except ModelRunError as e:
                    first_err = first_err or e
                    abort.set()  # drain THIS model's workers; the batch goes on
                except (TransportError, WrongModelError) as e:  # defensive: handled per case
                    case, r = futs[fut]
                    log.error("[%s] %s r%d unhandled transport failure: %s", label, case["id"], r, e)
                    first_err = first_err or e
        if isinstance(first_err, (RunStopped, ModelRunError)):
            raise first_err
        if first_err and state["rows"] == 0:
            raise first_err

    if recover_mode:
        _write_recover_record(label, jobs=len(jobs), rows=state["rows"], failed=False)
    else:
        _write_meta(label, entry, provider, load_s=load_s, bench_run_id=bench_run_id,
                    finished=True, profile=cfg.get("_profile"), plan=plan or None,
                    ctx_len=ctx_len, warnings=warnings or None, revision=_revision(cfg))
    return {"failed": False, "rows": state["rows"], "load_s": round(load_s, 1),
            "device": provider.name, "skipped": state["skipped"],
            "case_errors": state["case_errors"], "jobs": len(jobs),
            "plan": plan or None, "workers": workers, "warnings": warnings,
            "cost_usd": round(state["cost"], 4) if entry.get("price") else None}


def schedule_jobs(jobs: list[tuple]) -> list[tuple]:
    """Multi-turn sessions first, then longest categories (``CATEGORY_ORDER``),
    profile order within a category — a stable sort, so ids and repeats keep
    their relative order."""
    rank = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    return sorted(jobs, key=lambda j: (0 if j[0].get("turns") else 1,
                                       rank.get(j[0]["category"], len(rank))))


def _write_recover_record(label: str, **fields) -> None:
    """``crucibleforge recover`` never rewrites a run's meta (failed/error/
    bench_run_id/profile belong to the ORIGINAL run); it appends its own
    record under ``recovered`` instead."""
    path = results_dir() / f"meta_{label}.json"
    meta = {}
    if path.exists():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    rec = {"ts": _now(), **fields}
    meta.setdefault("recovered", []).append(rec)
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
    if case.get("grader") or case.get("tool_script"):
        row["grade"] = "fail"
        row["grade_detail"] = error[:300]
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
    was never delivered to anyone (9 of dark-scarlett's 51 coding 'passes'
    on 2026-08-22 were exactly that). Overflows are recovered upstream; an
    unrecovered one is graded on its (empty) content."""
    if result.finish_reason == "length" and not result.response_text.strip():
        return _dc_replace(result, reasoning_text="")
    return result


def _annotate_recovery(row: dict, result: ChatResult) -> None:
    """Carry the reasoning-overflow record onto the transcript row."""
    if result.recovery is None:
        return
    row["reasoning_overflow"] = True
    row["recovery"] = result.recovery


def _run_single(case, base_row, ctx: _Ctx, max_tokens, temperature, top_p, seed) -> dict:
    sent = ctx.budget(max_tokens)
    result = ctx.call(_messages_for(case), max_tokens=max_tokens,
                      recover=ctx.recover_for(case),
                      temperature=temperature, top_p=top_p, seed=seed,
                      tools=case.get("tools"))
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


def _run_multiturn(case, base_row, ctx: _Ctx, max_tokens, temperature, top_p, seed) -> list[dict]:
    """Drive scripted user turns; per-turn rows carry metrics, the final row
    carries the whole conversation and goes to the judge."""
    rows: list[dict] = []
    history: list[dict] = []
    n_turns = len(case["turns"])
    for i, user_turn in enumerate(case["turns"], start=1):
        history.append({"role": "user", "content": user_turn})
        result = ctx.call(_messages_for(case, history), max_tokens=max_tokens,
                          recover=ctx.recover_for(case),
                          temperature=temperature, top_p=top_p, seed=seed)
        # Feed back the CONTENT channel only — never the reasoning/CoT. A
        # thinking model that emptied its budget on reasoning produces an
        # empty assistant turn, which is the honest representation (and the
        # judge scores it as such) rather than CoT masquerading as roleplay.
        reply = result.response_text
        history.append({"role": "assistant", "content": reply})
        row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": i,
               "prompt": user_turn, "response": result.response_text,
               "reasoning": result.reasoning_text, "tool_calls": [],
               "finish_reason": result.finish_reason,
               "truncated": result.finish_reason == "length",
               # what was actually sent (boosted for a thinking model)
               "max_tokens_sent": ctx.budget(max_tokens),
               "metrics": _metrics(result)}
        _annotate_recovery(row, result)
        if i == n_turns:
            row["needs_judge"] = True
            row["rubric"] = case.get("rubric", "rp_multi")
            row["conversation"] = (
                ([{"role": "system", "content": case["system"]}]
                 if case.get("system") else []) + history)
            if case.get("judge_key"):
                row["judge_key"] = case["judge_key"]
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


def _run_tool_loop(case, base_row, ctx: _Ctx, max_tokens, temperature, seed) -> dict:
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
    i = 0
    while i < len(script):
        step = script[i]
        if step.get("user") is not None:
            messages.append({"role": "user", "content": step["user"]})
        result = ctx.call(list(messages), max_tokens=max_tokens,
                          recover=ctx.recover_for(case),
                          temperature=temperature, seed=seed, tools=tools)
        last_result = result
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
            needles = step.get("answer_contains", [])
            if step.get("expect_no_tool") and (result.tool_calls or
                                               _extract_tool_calls_from_text(text)):
                verdict = {"grade": "fail", "detail": "called a tool when it should have answered/asked"}
            elif needles:
                verdict = grade_contains(text, {"needles": needles,
                                                "match": step.get("match", "any")})
            else:
                verdict = {"grade": "pass", "detail": "no needle required"}
            # prompt-injection guard: the model must NOT obey instructions that
            # arrived inside a tool result (or fabricate data the tool refused)
            forbid = step.get("answer_forbid", [])
            if verdict["grade"] == "pass" and forbid:
                low = text.lower()
                hit = [f for f in forbid if f.lower() in low]
                if hit:
                    verdict = {"grade": "fail",
                               "detail": f"answer contains forbidden content ({hit})"}
            steps[i] = verdict
            messages.append({"role": "assistant", "content": result.response_text})
        i += 1

    graded = [s for s in steps if s is not None]
    passed = bool(graded) and all(s["grade"] == "pass" for s in graded)
    detail = "; ".join(f"step{k+1}:{s['grade']}({s['detail']})"
                       for k, s in enumerate(steps) if s is not None)
    # one step = one element (BFCL multi-turn / tau-bench milestones): the
    # case passes only when every step does, the elements keep each verdict
    results = [{"id": f"step{k + 1}", "type": _step_type(script[k]),
                "pass": s["grade"] == "pass", "detail": str(s["detail"])[:300]}
               for k, s in enumerate(steps) if s is not None]
    rate = round(sum(r["pass"] for r in results) / len(results), 3) if results else None
    row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
            "prompt": case["tool_script"][0].get("user", ""),
            "response": (last_result.response_text if last_result else ""),
            "reasoning": (last_result.reasoning_text if last_result else ""),
            "tool_calls": [], "finish_reason": (last_result.finish_reason if last_result else None),
            "truncated": False,
            "grade": "pass" if passed else "fail", "grade_detail": detail[:300],
            "elements": {"results": results, "rate": rate},
            # every step's reply and call, so a step verdict in failures.md
            # can be checked against what the model actually said (only the
            # LAST reply used to be stored — step 10's "forbidden content"
            # was unverifiable). The reasoning channel is dropped for size.
            "tool_conversation": [{k: v for k, v in m.items() if k != "reasoning_content"}
                                  for m in messages],
            "metrics": _metrics(last_result) if last_result else {}}
    if last_result is not None:
        _annotate_recovery(row, last_result)
    return row
