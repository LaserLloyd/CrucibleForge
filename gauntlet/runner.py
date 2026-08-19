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
from .api import ChatResult, TransportError, WrongModelError
from .config import CSV_COLUMNS, results_dir, append_transcript, repeats_for
from .graders import (_extract_tool_calls_from_text, grade, grade_contains,
                      grade_tool_call, prose_metrics)
from .providers import Provider, cost_usd, model_extra_body, provider_for
from .version import revision as _revision

log = logging.getLogger(__name__)

CREATIVE_SEEDS = [41, 42, 43, 44, 45]
SANITY_CHECK_AFTER = 3

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
        d = cfg.get("defaults", {})
        self.think_factor = float(d.get("thinking_max_tokens_factor", 4))
        self.think_cap = int(d.get("thinking_max_tokens_cap", 32768))

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
        """One tiny probe so ``thinking`` is settled before concurrent cases
        start (otherwise the first few would run on the un-boosted budget)."""
        if self.thinking is not None:
            return
        try:
            r = self.provider.chat(self.model_id, [{"role": "user", "content": "Reply with OK."}],
                                   max_tokens=64, temperature=0.0, seed=42,
                                   extra_body=self.extra_body)
        except (TransportError, WrongModelError) as e:
            log.warning("thinking probe failed (%s) — will auto-detect from replies", e)
            return
        self._observe(r)
        if self.thinking is None:
            self.thinking = False  # probe showed no reasoning channel

    def call(self, messages, *, budgeted: bool = False, **kw) -> ChatResult:
        """One completion; on eviction/wrong-model, reload once and retry.
        budgeted=True means max_tokens is already the value to send."""
        kw.setdefault("extra_body", self.extra_body)
        if "max_tokens" in kw and not budgeted:
            kw["max_tokens"] = self.budget(kw["max_tokens"])
        try:
            r = self.provider.chat(self.model_id, messages, **kw)
        except WrongModelError as e:
            log.warning("%s — reloading and retrying once", e)
            with self.lock:
                self.provider.switch_model(self.model_id, self.ctx_len)
            r = self.provider.chat(self.model_id, messages, **kw)
        self._observe(r)
        return r


def run_models(cfg: dict, model_entries: list[dict], cases: list[dict],
               smoke: bool = False) -> dict:
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
                summary[label] = _run_one_model(cfg, entry, cases, csvw, smoke)
            except RunStopped:
                summary[label] = {"failed": True, "error": "stopped by user"}
                _write_meta(label, entry, provider_for(cfg, entry), failed=True,
                            error="stopped by user")
                break
            except (ModelRunError, TransportError, WrongModelError,
                    lms.LmsError, studioforge.StudioForgeError) as e:
                log.error("model %s FAILED: %s — continuing with next model", label, e)
                summary[label] = {"failed": True, "error": str(e)[:500]}
                _write_meta(label, entry, provider_for(cfg, entry), failed=True,
                            error=str(e)[:500])
    finally:
        csvw.close()
    return summary


def _write_meta(label: str, entry: dict, provider: Provider, *, load_s: float | None = None,
                bench_run_id: str | None = None, failed: bool = False,
                error: str | None = None, finished: bool = False,
                profile: str | None = None) -> None:
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
    if bench_run_id:
        meta["bench_run_id"] = bench_run_id
    if error:
        meta["error"] = error
    if finished:
        meta["finished"] = _now()
    results_dir().mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2))


def _run_one_model(cfg, entry, cases, csvw: _Csv, smoke) -> dict:
    label = entry["name"]
    model_id = entry["model_id"]
    ctx_len = entry.get("context_length") or cfg["defaults"].get("context_length")
    provider = provider_for(cfg, entry)
    bench_run_id = str(uuid.uuid4())[:8]
    ctx = _Ctx(cfg, entry, provider, ctx_len)

    log.info("=== %s (%s) via %s [%s], ctx=%s ===", label, model_id,
             provider.name, provider.type, ctx_len)
    if not provider.is_available(model_id):
        raise ModelRunError(f"{model_id} is not served by provider {provider.name}")
    load_s = provider.switch_model(model_id, ctx_len)
    _write_meta(label, entry, provider, load_s=load_s, bench_run_id=bench_run_id,
                profile=cfg.get("_profile"))
    ctx.detect_thinking()

    state = {"rows": 0, "sanity_total": 0, "sanity_empty": 0,
             "speed_samples": [], "speed_gated": False, "skipped": 0,
             "cost": 0.0}
    min_tps = float(cfg["defaults"].get("min_tok_per_s", 0) or 0)
    revision = _revision(cfg)

    def base_row_for(case, repeat, seed, temperature, top_p, max_tokens):
        return {
            "bench_run_id": bench_run_id, "bench_revision": revision,
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
                if row.get("skipped"):
                    continue
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

    def run_case_repeat(case, repeat):
        if STOP.is_set():
            raise RunStopped()
        cat = case["category"]
        temperature = case.get("temperature", 0.0)
        top_p = case.get("top_p", 1.0)
        max_tokens = case["max_tokens"]
        seed = CREATIVE_SEEDS[(repeat - 1) % len(CREATIVE_SEEDS)] if temperature > 0 else 42
        base_row = base_row_for(case, repeat, seed, temperature, top_p, max_tokens)

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

        if case.get("tool_script"):
            rows = [_run_tool_loop(case, base_row, ctx, max_tokens, temperature, seed)]
        elif case.get("turns"):
            rows = _run_multiturn(case, base_row, ctx, max_tokens, temperature, top_p, seed)
        else:
            rows = [_run_single(case, base_row, ctx, max_tokens, temperature, top_p, seed)]
        persist(rows)
        log.info("[%s] %s r%d done (%d rows)", label, case["id"], repeat, state["rows"])

    # perf first, serially, with a warmup so the first TTFT isn't cold-start
    perf_cases = [c for c in cases if c["category"] == "perf"]
    other_cases = [c for c in cases if c["category"] != "perf"]
    if perf_cases:
        ctx.call([{"role": "user", "content": "Hi"}], max_tokens=8,
                 temperature=0.0, seed=42)
        for case in perf_cases:
            for repeat in range(1, repeats_for(cfg, "perf", smoke) + 1):
                run_case_repeat(case, repeat)

    jobs = [(c, r) for c in other_cases
            for r in range(1, repeats_for(cfg, c["category"], smoke) + 1)]
    workers = provider.workers(model_id)
    if workers <= 1:
        for case, repeat in jobs:
            run_case_repeat(case, repeat)
    else:
        log.info("running %d jobs with concurrency=%d", len(jobs), workers)
        first_err = None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(run_case_repeat, c, r): (c, r) for c, r in jobs}
            for fut in as_completed(futs):
                try:
                    fut.result()
                except (ModelRunError, RunStopped) as e:
                    first_err = first_err or e
                    STOP.set()  # drain remaining workers quickly
                except (TransportError, WrongModelError) as e:
                    case, r = futs[fut]
                    log.error("[%s] %s r%d transport failure: %s", label, case["id"], r, e)
                    first_err = first_err or e
        if isinstance(first_err, RunStopped):
            raise first_err
        if isinstance(first_err, ModelRunError):
            raise first_err
        if first_err and state["rows"] == 0:
            raise first_err
        # a partial concurrent run: clear STOP only if we set it ourselves for a
        # ModelRunError — for a user stop, leave it so remaining models are skipped
        if first_err and not isinstance(first_err, RunStopped):
            STOP.clear()

    _write_meta(label, entry, provider, load_s=load_s, bench_run_id=bench_run_id,
                finished=True, profile=cfg.get("_profile"))
    return {"failed": False, "rows": state["rows"], "load_s": round(load_s, 1),
            "device": provider.name, "skipped": state["skipped"],
            "cost_usd": round(state["cost"], 4) if entry.get("price") else None}


def _run_single(case, base_row, ctx: _Ctx, max_tokens, temperature, top_p, seed) -> dict:
    sent = ctx.budget(max_tokens)
    result = ctx.call(_messages_for(case), max_tokens=sent, budgeted=True,
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
    verdict = grade(result, case)
    if verdict:
        row["grade"] = verdict["grade"]
        detail = verdict["detail"]
        if verdict["grade"] == "fail" and result.finish_reason == "length":
            # be honest about WHY: a truncated reply is a budget/verbosity
            # failure, not necessarily a wrong answer
            detail = (f"TRUNCATED at {sent} tokens "
                      f"({result.reasoning_tokens or 0} reasoning); {detail}")
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
            verdict = grade_tool_call(result.tool_calls, result.scoreable_text(),
                                      {"expect_tool": step["expect_tool"],
                                       "required_args": step.get("required_args", {}),
                                       "max_calls": step.get("max_calls", 1)})
            steps.append(verdict)
            # feed the model's tool call + our canned result back into context;
            # fall back to a text-emitted call so a thinking model's call that
            # failed the wire grammar still survives the loop
            calls = result.tool_calls or _extract_tool_calls_from_text(
                result.scoreable_text())
            call = calls[0] if calls else None
            call_id = (call or {}).get("id") or "call_0"
            messages.append({"role": "assistant", "content": "",
                             "tool_calls": [{"id": call_id, "type": "function",
                                             "function": {"name": (call or {}).get("name", ""),
                                                          "arguments": (call or {}).get("arguments", "{}")}}]})
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": step["tool_result"]})
        else:
            text = result.scoreable_text()
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
    return {**base_row, "run_id": str(uuid.uuid4())[:8], "turn": None,
            "prompt": case["tool_script"][0].get("user", ""),
            "response": (last_result.response_text if last_result else ""),
            "reasoning": (last_result.reasoning_text if last_result else ""),
            "tool_calls": [], "finish_reason": (last_result.finish_reason if last_result else None),
            "truncated": False,
            "grade": "pass" if passed else "fail", "grade_detail": detail[:300],
            "metrics": _metrics(last_result) if last_result else {}}
