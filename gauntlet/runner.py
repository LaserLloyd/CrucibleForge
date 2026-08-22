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
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from . import lms, studioforge
from dataclasses import replace as _dc_replace

from .api import (ChatResult, GenerationRejected, RequestRejected, TransportError,
                  WrongModelError)
from .config import CSV_COLUMNS, results_dir, append_transcript, repeats_for
from .graders import (_extract_tool_calls_from_text, grade, grade_contains,
                      grade_tool_call, prose_metrics)
from .providers import Provider, cost_usd, merge_extra_body, model_extra_body, provider_for
from .version import judge_fingerprint as _judge_fp, revision as _revision

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
        self.f = open(path, "a", newline="")
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
        # defaults.reasoning_overflow_recovery (bool, default on); a model
        # entry can opt out with recovery: false
        self.recovery_enabled = (bool(d.get("reasoning_overflow_recovery", True))
                                 and bool(entry.get("recovery", True)))
        # flips to False the first time the provider rejects chat_template_kwargs
        self.no_think_supported = True

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

    def call(self, messages, *, max_tokens: int, recover: bool = True, **kw) -> ChatResult:
        """One completion on the CASE budget ``max_tokens`` (boosted for a
        thinking model), with reasoning-overflow recovery. ``recover=False``
        for timing rows (perf), whose metrics must stay single-shot."""
        kw.setdefault("extra_body", self.extra_body)
        first = self._chat(messages, max_tokens=self.budget(max_tokens), **kw)
        if not (recover and self.recovery_enabled and self._overflowed(first)):
            return first
        first_info = {"finish_reason": first.finish_reason,
                      "completion_tokens": first.completion_tokens,
                      "reasoning_tokens": first.reasoning_tokens,
                      "reasoning_chars": len(first.reasoning_text),
                      "max_tokens_sent": self.budget(max_tokens)}
        log.info("%s: reasoning overflow (%s reasoning tokens, no answer) — "
                 "recovering the answer on a %d-token budget", self.model_id,
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
        if error is None and (r is None or (not self._answered(r) and r.finish_reason == "length")):
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
                              "answer_max_tokens": max_tokens}
            if error:
                first.recovery["error"] = error
            log.warning("%s: reasoning overflow NOT recovered after %d attempt(s)",
                        self.model_id, attempts)
            self._bill(first, spent)
            return first
        r.recovery = {"mode": mode, "attempts": attempts, "first": first_info,
                      "answer_max_tokens": max_tokens}
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
    by ``gauntlet recover``)."""
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
                                error="stopped by user")
                break
            except (ModelRunError, TransportError, WrongModelError,
                    lms.LmsError, studioforge.StudioForgeError) as e:
                log.error("model %s FAILED: %s — continuing with next model", label, e)
                summary[label] = {"failed": True, "error": str(e)[:500]}
                if jobs_by_label and label in jobs_by_label:
                    _write_recover_record(label, failed=True, error=str(e)[:500])
                else:
                    _write_meta(label, entry, provider_for(cfg, entry), failed=True,
                                error=str(e)[:500])
    finally:
        csvw.close()
    return summary


def _write_meta(label: str, entry: dict, provider: Provider, *, load_s: float | None = None,
                bench_run_id: str | None = None, failed: bool = False,
                error: str | None = None, finished: bool = False,
                profile: str | None = None, plan: dict | None = None,
                ctx_len: int | None = None) -> None:
    path = results_dir() / f"meta_{label}.json"
    meta = {}
    if path.exists():
        try:
            meta = json.loads(path.read_text())
        except json.JSONDecodeError:
            meta = {}
    meta.update({
        "model_label": label, "model_id": entry["model_id"],
        "device": provider.name, "provider": provider.name,
        "provider_type": provider.type,
        "updated": _now(), "failed": failed,
    })
    if entry.get("price"):
        meta["price"] = entry["price"]
    if profile:
        meta["profile"] = profile
    if load_s is not None:
        meta["load_s"] = round(load_s, 1)
    if plan:
        meta["plan"] = plan  # the placement the numbers were measured under
    if ctx_len:
        meta["context_length"] = int(ctx_len)
    if bench_run_id:
        meta["bench_run_id"] = bench_run_id
    if error:
        meta["error"] = error
    if finished:
        meta["finished"] = _now()
    results_dir().mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2))


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
                    profile=cfg.get("_profile"), plan=plan or None, ctx_len=ctx_len)
    ctx.detect_thinking()

    state = {"rows": 0, "sanity_total": 0, "sanity_empty": 0,
             "speed_samples": [], "speed_gated": False, "skipped": 0,
             "cost": 0.0, "consecutive_errors": 0, "case_errors": 0}
    min_tps = float(cfg["defaults"].get("min_tok_per_s", 0) or 0)
    revision = _revision(cfg)
    judge_fp = _judge_fp(cfg)

    def base_row_for(case, repeat, seed, temperature, top_p, max_tokens, rid=None):
        return {
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
        cat = case["category"]
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
            # args etc.) — that is this case failing, not the run
            log.warning("[%s] %s r%d: server rejected the model's generation: %s",
                        label, case["id"], repeat, str(e)[:200])
            persist([_error_row(base_row, case, f"server rejected the model's output: {e}")])
            return
        except (TransportError, WrongModelError) as e:
            with ctx.lock:
                state["consecutive_errors"] += 1
                state["case_errors"] += 1
                n = state["consecutive_errors"]
            log.error("[%s] %s r%d transport failure (%d in a row): %s",
                      label, case["id"], repeat, n, str(e)[:200])
            persist([_error_row(base_row, case, f"transport failure: {e}")])
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
    workers = provider.workers(model_id)
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
                    ctx_len=ctx_len)
    return {"failed": False, "rows": state["rows"], "load_s": round(load_s, 1),
            "device": provider.name, "skipped": state["skipped"],
            "case_errors": state["case_errors"], "jobs": len(jobs),
            "plan": plan or None,
            "cost_usd": round(state["cost"], 4) if entry.get("price") else None}


def _write_recover_record(label: str, **fields) -> None:
    """``gauntlet recover`` never rewrites a run's meta (failed/error/
    bench_run_id/profile belong to the ORIGINAL run); it appends its own
    record under ``recovered`` instead."""
    path = results_dir() / f"meta_{label}.json"
    meta = {}
    if path.exists():
        try:
            meta = json.loads(path.read_text())
        except json.JSONDecodeError:
            meta = {}
    rec = {"ts": _now(), **fields}
    meta.setdefault("recovered", []).append(rec)
    results_dir().mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2))


def _error_row(base_row: dict, case: dict, error: str) -> dict:
    """A persisted FAILED row for a case whose generation never arrived, so
    the case counts against the model instead of silently vanishing."""
    first_user = (case.get("tool_script") or [{}])[0].get("user", "")
    row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
           "system": case.get("system"),
           "prompt": case.get("prompt") or first_user,
           "response": "", "reasoning": "", "tool_calls": [], "finish_reason": "error",
           "truncated": False, "error": error[:500], "metrics": {}}
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
    judge verdict, so ``gauntlet judge`` re-scores them."""
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
                      recover=case["category"] != "perf",
                      temperature=temperature, top_p=top_p, seed=seed,
                      tools=case.get("tools"))
    row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
           "system": case.get("system"),
           "prompt": case["prompt"], "response": result.response_text,
           "reasoning": result.reasoning_text, "tool_calls": result.tool_calls,
           "finish_reason": result.finish_reason,
           "truncated": result.finish_reason == "length",
           "max_tokens_sent": sent,
           "answered_in_reasoning": (not result.response_text.strip()
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
        if verdict.get("needs_judge"):
            # reference-answer grading: a (small) LLM judge compares the
            # model's answer to the gold answer — see judge.RUBRICS["reference"]
            row["needs_judge"] = True
            row["rubric"] = "reference"
            row["reference"] = verdict.get("reference")
    if case.get("rubric"):
        row["needs_judge"] = True
        row["rubric"] = case["rubric"]
    # objective prose metrics for creative writing (de-loads the judge)
    if case["category"] in ("rp", "nsfw") and result.response_text.strip():
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
               "metrics": _metrics(result)}
        _annotate_recovery(row, result)
        if i == n_turns:
            row["needs_judge"] = True
            row["rubric"] = case.get("rubric", "rp_multi")
            row["conversation"] = (
                ([{"role": "system", "content": case["system"]}]
                 if case.get("system") else []) + history)
        rows.append(row)
    return rows


def _run_tool_loop(case, base_row, ctx: _Ctx, max_tokens, temperature, seed) -> dict:
    """Agent tool loop: model calls a tool, we inject a canned tool result,
    the model must incorporate it into a final answer. Grades both steps
    (right call, then answer uses the returned data)."""
    script = case["tool_script"]
    tools = case.get("tools")
    messages: list[dict] = []
    if case.get("system"):
        messages.append({"role": "system", "content": case["system"]})

    steps: list[dict] = []
    last_result = None
    for step in script:
        if step.get("user") is not None:
            messages.append({"role": "user", "content": step["user"]})
        result = ctx.call(list(messages), max_tokens=max_tokens,
                          temperature=temperature, seed=seed, tools=tools)
        last_result = result
        if step.get("expect_tool"):
            verdict = grade_tool_call(result.tool_calls, _answer_view(result).scoreable_text(),
                                      {"expect_tool": step["expect_tool"],
                                       "required_args": step.get("required_args", {}),
                                       "max_calls": step.get("max_calls", 1)})
            steps.append(verdict)
            # feed the model's tool call + our canned result back into context;
            # fall back to a text-emitted call so a thinking model's call that
            # failed the wire grammar still survives the loop
            calls = result.tool_calls or _extract_tool_calls_from_text(
                _answer_view(result).scoreable_text())
            call = calls[0] if calls else None
            call_id = (call or {}).get("id") or "call_0"
            messages.append({"role": "assistant", "content": "",
                             "tool_calls": [{"id": call_id, "type": "function",
                                             "function": {"name": (call or {}).get("name", ""),
                                                          "arguments": (call or {}).get("arguments", "{}")}}]})
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": step["tool_result"]})
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
            steps.append(verdict)
            messages.append({"role": "assistant", "content": result.response_text})

    passed = all(s["grade"] == "pass" for s in steps)
    detail = "; ".join(f"step{i+1}:{s['grade']}({s['detail']})"
                       for i, s in enumerate(steps))
    row = {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
            "prompt": case["tool_script"][0].get("user", ""),
            "response": (last_result.response_text if last_result else ""),
            "reasoning": (last_result.reasoning_text if last_result else ""),
            "tool_calls": [], "finish_reason": (last_result.finish_reason if last_result else None),
            "truncated": False,
            "grade": "pass" if passed else "fail", "grade_detail": detail[:300],
            "metrics": _metrics(last_result) if last_result else {}}
    if last_result is not None:
        _annotate_recovery(row, last_result)
    return row
