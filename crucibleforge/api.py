"""OpenAI-compatible streaming client (LM Studio, llama.cpp, DeepSeek, OpenRouter, ...) with real usage metrics.

Hard lessons from the v1 draft baked in:
- LM Studio answers HTTP 200 with a completion from WHATEVER model is loaded,
  even for a bogus/unloaded model id. Every response is therefore verified
  against the requested id and a WrongModelError is raised on mismatch —
  this exact failure silently zeroed the entire v1 judge run.
- Token counts come from the server's `usage` payload
  (stream_options.include_usage), never from chars/4 guessing.
- tok/s needs >= 2 completion tokens to be meaningful; otherwise None.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

MAX_TRANSPORT_RETRIES = 3
RETRY_BACKOFF_S = 2.0
# A StudioForge priority hold (a chat/agent-tier model is LOADING, so our
# background traffic is refused) is a wait, not a failure: the load takes as
# long as a big model takes to come up, which is minutes, not the ~30 s the
# three transport retries above buy. Held requests therefore wait on their own
# budget and do not spend a retry — otherwise a family bot warming its model
# would score a run's worth of cases as transport failures and, three in a row,
# abort the model run outright.
PRIORITY_HOLD_WAIT_S = 600.0
# Fallback pause when a hold arrives with no hint (the server sends 15 s).
PRIORITY_HOLD_POLL_S = 15.0

# Thinking models (gemma `(think)`, deepseek/qwen `<think>`, qwen3
# `<|thinking|>`) emit CoT wrapped in these delimiters. When the server fails
# to extract reasoning into a separate channel (llama.cpp `auto` detection
# misses e.g. merged/abliterated qwen templates), the tags LEAK into content
# and corrupt tool-call JSON, instruction-following output and code. We strip
# them at the API boundary so every consumer (graders, judge, transcripts)
# sees only the actual response.
_THINK_TAGS = re.compile(
    r"<think>|</think>|<\|thinking\|>|<\|/thinking\|>|</\|thinking\|>|\(think\)",
    re.IGNORECASE)


def split_thinking(text: str) -> tuple[str, str]:
    """(visible answer, inline thinking). Same parsing as
    ``strip_thinking_tags`` but the thinking blocks are KEPT, not dropped:
    a provider that sends its reasoning inline in ``content`` (MiniMax-M3's
    ``<think>…</think>``) must still land it in ``reasoning_text``, or a reply
    that thought its whole budget away looks like an empty answer with no
    reasoning at all and overflow recovery never fires (minimax-m3 NX1,
    2026-09-24: 8000 tokens, finish=length, content and reasoning both "")."""
    if not text:
        return text, ""
    out: list[str] = []
    think: list[str] = []
    pos = 0
    while True:
        m = _THINK_TAGS.search(text, pos)
        if not m:
            out.append(text[pos:])
            break
        tag = m.group(0).lower()
        if tag == "(think)":
            out.append(text[pos:m.start()])
            close = _THINK_TAGS.search(text, m.end())
            if not close:
                think.append(text[m.end():])
                break
            think.append(text[m.end():close.start()])
            pos = close.end()
        elif tag in ("<think>", "<|thinking|>"):
            out.append(text[pos:m.start()])
            close_tags = ("</think>",) if tag == "<think>" else ("<|/thinking|>", "</|thinking|>")
            ends = [e for e in (text.find(c, m.end()) for c in close_tags) if e >= 0]
            if not ends:
                think.append(text[m.end():])
                break
            end = min(ends)
            think.append(text[m.end():end])
            pos = end + len(close_tags[ends.index(end)])
        else:
            out.append(text[pos:m.start()])
            pos = m.end()
    return "".join(out), "\n".join(t.strip("\n") for t in think)


def strip_thinking_tags(text: str) -> str:
    """Remove thinking blocks from a completion before any parsing.

    Handles deepseek/qwen `<think>...</think>`, qwen3 `<|thinking|>...`
    `<|/thinking|>` and gemma `(think) ... (think)` (same delimiter opens and
    closes). A truncated, unclosed opening tag drops the remainder — an
    unfinished thinking block is reasoning, never an answer. Stray closing
    tags (`</think>` with no opener) are dropped as bare tokens. Idempotent.
    """
    if not text:
        return text
    out: list[str] = []
    pos = 0
    while True:
        m = _THINK_TAGS.search(text, pos)
        if not m:
            out.append(text[pos:])
            break
        tag = m.group(0).lower()
        if tag == "(think)":
            # gemma: the same delimiter opens AND closes the thinking block
            out.append(text[pos:m.start()])
            close = _THINK_TAGS.search(text, m.end())
            if not close:
                break  # unclosed: reasoning runs to the end of the text
            pos = close.end()
        elif tag in ("<think>", "<|thinking|>"):
            out.append(text[pos:m.start()])
            # qwen3's close token renders as `<|/thinking|>` in some templates
            # and `</|thinking|>` in others — accept both
            close_tags = ("</think>",) if tag == "<think>" else ("<|/thinking|>", "</|thinking|>")
            ends = [e for e in (text.find(c, m.end()) for c in close_tags) if e >= 0]
            if not ends:
                break  # unclosed: drop the remainder
            end = min(ends)
            pos = end + len(close_tags[ends.index(end)])
        else:
            # stray closer (</think>, <|/thinking|>) — drop just the tag
            out.append(text[pos:m.start()])
            pos = m.end()
    return "".join(out)


class TransportError(RuntimeError):
    """Server unreachable / HTTP error / stream died."""


class StreamStalled(TransportError):
    """No bytes at all from the server for ``stall_s`` seconds mid-request: a
    stuck connection or a wedged slot, not a slow model (a model that is
    still generating streams tokens continuously). Not retried — the case is
    recorded as errored and the run moves on."""


class RowTimeout(TransportError):
    """The request passed its total wall-clock ceiling (``max_wall_s``) —
    used for judge calls, whose per-row budget keeps one looping verdict from
    holding the judge phase. Not retried."""


class RequestRejected(TransportError):
    """HTTP 4xx other than 408/429: the request itself is wrong (bad model id,
    unsupported parameter, auth). Retrying verbatim cannot help — callers
    that can adapt (e.g. drop response_format) do so on this type."""

    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class GenerationRejected(TransportError):
    """The server could not deliver what the model generated — e.g. llama-server
    answers HTTP 500 "Failed to parse tool call arguments as JSON" when the
    model emits malformed tool-call arguments. That is a property of the
    model's OUTPUT, not of the transport: retrying the identical request
    reproduces it (deterministic at temperature 0), and it must be scored as a
    failed case rather than abort a whole model run (joyfox-35b-rp lost two
    full runs to exactly this on 2026-08-19)."""


# Server-side messages that mean "your model produced something I can't
# deliver" rather than "I am broken". Matched case-insensitively on the body.
_GENERATION_REJECT_MARKERS = (
    "failed to parse tool call",
    "failed to parse tool_call",
    "invalid tool call",
    "tool call arguments",
    # llama-server cannot build a tool/reasoning parser for the model's chat
    # template (HTTP 400, sometimes relayed inside a 5xx/SSE error): that case
    # is ERRORED (runner._generation_reject_kind), the run goes on
    # (precog-123b aborted a whole run on three of these in a row)
    "unable to generate parser",
)


class VramContention(TransportError):
    """The server could not place the model right now (StudioForge 507
    ``insufficient_vram`` / 503 with ``retry_after_s``): a resident is busy or
    the window does not fit. Carries the server's own wait hint and per-mode
    suggestions so callers wait the right amount instead of a blind backoff."""

    def __init__(self, msg: str, *, status: int | None = None,
                 retry_after_s: float | None = None, suggestions=None, body: str = ""):
        super().__init__(msg)
        self.status = status
        self.retry_after_s = retry_after_s
        self.suggestions = suggestions
        self.body = body


class PriorityHold(VramContention):
    """StudioForge 503 ``priority_hold``: a chat- (tier 1) or agent-tier (2)
    model is loading, and loads plus inference for worse-tier models are held
    off until it is serving (StudioForge D46/D48). Purely transient — the
    holder is named in ``model_id``/``priority`` — so it is waited out rather
    than counted as a case failure. Before the code existed this arrived as a
    generic ``model_busy`` 503, which is why it stays a VramContention: older
    rigs give us the same retry hint under a different name."""

    def __init__(self, msg: str, *, model_id: str | None = None,
                 priority: int | None = None, **kw):
        super().__init__(msg, **kw)
        self.model_id = model_id
        self.priority = priority

    def holder(self) -> str:
        who = self.model_id or "an unnamed model"
        return f"{who} (tier {self.priority})" if self.priority else who


def _error_holders(body: str) -> list[dict]:
    """The dicts a structured error body may hang its detail off: the envelope,
    ``error`` inside it, and StudioForge's additive ``error.studioforge``."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        # the SSE path wraps the error object: "server error: {...}"
        i = (body or "").find("{")
        if i < 0:
            return []
        try:
            data = json.loads(body[i:])
        except ValueError:
            return []
    holders = [data]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            holders.append(err)
            if isinstance(err.get("studioforge"), dict):
                holders.append(err["studioforge"])
    return [h for h in holders if isinstance(h, dict)]


def _parse_error_hints(body: str) -> tuple[float | None, object]:
    """(retry_after_s, suggestions) from a JSON error body, if any."""
    holders = _error_holders(body)
    ra = None
    sugg = None
    for h in holders:
        if isinstance(h, dict):
            if ra is None and h.get("retry_after_s") is not None:
                try:
                    ra = float(h["retry_after_s"])
                except (TypeError, ValueError):
                    pass
            if sugg is None and h.get("suggestions") is not None:
                sugg = h["suggestions"]
    return ra, sugg


def _parse_priority_hold(body: str) -> dict | None:
    """``{model_id, priority}`` when the body is a StudioForge ``priority_hold``
    refusal, else None. The code is authoritative (it is stable); the holder
    details are best-effort, since only the message is guaranteed prose."""
    holders = _error_holders(body)
    if not any(h.get("code") == "priority_hold" for h in holders):
        return None
    for h in holders:
        hold = h.get("priority_hold")
        if isinstance(hold, dict):
            return hold
        busy = h.get("busy")
        if isinstance(busy, dict) and isinstance(busy.get("priority_hold"), dict):
            return busy["priority_hold"]
    return {}


def _error_code(body: str) -> str | None:
    """The stable ``error.code`` from a structured error body, or None. This
    is what the rig-integration contract says to branch on —
    never on prose — and it is the only signal available on the SSE-embedded
    error path, where the transport status code itself is None."""
    for h in _error_holders(body):
        if h.get("code"):
            return h["code"]
    return None


def _busy_models(body: str) -> list | None:
    """The ``busy_models`` hint on a 507: models that WOULD free the VRAM but
    are mid-request elsewhere. Its presence is what turns a 507 from "the box
    is full" into "the box is busy, not full"."""
    for h in _error_holders(body):
        bm = h.get("busy_models")
        if isinstance(bm, list):
            return bm
    return None


class VramExhausted(TransportError):
    """StudioForge 507 that is genuinely full, not merely busy: no
    ``busy_models`` hint and no retry hint. Per the rig-integration contract,
    retrying unchanged cannot succeed — this is terminal,
    never retried by ``stream_chat_retried``. Carries ``suggestions`` /
    ``max_ctx_that_fits`` for the caller to act on (shorten the request,
    reload narrower, or stand down)."""

    def __init__(self, msg: str, *, status: int | None = None, suggestions=None, body: str = ""):
        super().__init__(msg)
        self.status = status
        self.suggestions = suggestions
        self.body = body


def classify_server_error(status: int | None, body: str,
                          retry_after_header: str | None = None) -> TransportError:
    """Map a 5xx / SSE error payload to the right TransportError subclass.

    Branches on the structured ``error.code`` only — never on prose (the
    rig-integration contract). A 507 ``insufficient_vram`` is
    genuinely full and terminal UNLESS it also carries ``busy_models``
    (residents that would free the VRAM but are mid-request — the box is
    busy, not full) or a retry hint (``retry_after_s`` / ``Retry-After``), in
    which case waiting is correct and it is treated the same as a 503."""
    low = (body or "").lower()
    msg = f"HTTP {status}: {body[:500]}" if status else body[:500]
    if any(m in low for m in _GENERATION_REJECT_MARKERS):
        return GenerationRejected(msg)
    ra, sugg = _parse_error_hints(body)
    if ra is None and retry_after_header:
        try:
            ra = float(retry_after_header)
        except ValueError:
            pass
    hold = _parse_priority_hold(body)
    if hold is not None:
        return PriorityHold(msg, status=status, retry_after_s=ra, suggestions=sugg, body=body,
                            model_id=hold.get("model_id"), priority=hold.get("priority"))
    code = _error_code(body)
    if code == "insufficient_vram" or status == 507:
        # Structured fields decide this, never prose. The
        # SSE-embedded error path never carries a transport status (status is
        # None there), so ``code`` alone must be enough to recognise it.
        # busy_models non-empty means the box is busy, not full: the models
        # that would free the VRAM are mid-request elsewhere, so waiting is
        # correct. A retry hint (server-computed) means the same. Neither one
        # present means genuinely full (or a lease refusal, e.g. gpu_leased,
        # that never got as far as arithmetic) — retrying unchanged cannot
        # succeed.
        if _busy_models(body) or ra is not None:
            return VramContention(msg, status=status, retry_after_s=ra, suggestions=sugg, body=body)
        return VramExhausted(msg, status=status, suggestions=sugg, body=body)
    if status == 503:
        return VramContention(msg, status=status, retry_after_s=ra, suggestions=sugg, body=body)
    return TransportError(msg)


class WrongModelError(RuntimeError):
    """The server answered with a different model than requested."""


@dataclass
class ChatResult:
    response_text: str = ""
    reasoning_text: str = ""
    tool_calls: list = field(default_factory=list)
    served_model: str | None = None
    finish_reason: str | None = None
    ttft_s: float | None = None
    gen_s: float | None = None
    total_s: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    tok_per_s: float | None = None
    prompt_tok_per_s: float | None = None
    # set by the runner when the answer came from a reasoning-overflow
    # recovery pass (see runner._Ctx.call); None for a plain completion
    recovery: dict | None = None

    def scoreable_text(self) -> str:
        """The model's effective answer. Thinking models sometimes emit the
        whole answer in the reasoning channel with empty content.
        Thinking tags are stripped from whichever channel is used."""
        if self.finish_reason == "length" and not self.response_text.strip():
            # an unfinished thinking block is reasoning, never an answer
            return ""
        return strip_thinking_tags(
            self.response_text.strip() or self.reasoning_text.strip())


def _merge_tool_call_deltas(raw_deltas: list[dict]) -> list[dict]:
    calls: dict[int, dict] = {}
    for d in raw_deltas:
        idx = d.get("index", 0)
        slot = calls.setdefault(idx, {"id": None, "name": "", "arguments": ""})
        if d.get("id"):
            slot["id"] = d["id"]
        fn = d.get("function") or {}
        if fn.get("name"):
            slot["name"] += fn["name"]
        if fn.get("arguments"):
            slot["arguments"] += fn["arguments"]
    return [calls[k] for k in sorted(calls)]


def stream_chat(base_url: str, api_key: str, model: str, messages: list[dict], *,
                max_tokens: int, temperature: float = 0.0, top_p: float = 1.0,
                seed: int = 42, tools: list | None = None,
                response_format: dict | None = None,
                reasoning_format: str | None = None,
                extra_body: dict | None = None,
                headers: dict | None = None,
                priority: int | None = None,
                verify_model: bool = True,
                timeout: float = 900.0,
                stall_s: float | None = None,
                max_wall_s: float | None = None) -> ChatResult:
    """One streaming chat completion with timing + real token usage.

    reasoning_format (llama.cpp server param, e.g. "deepseek") forces CoT
    extraction into the reasoning_content channel instead of inline thinking
    tags in content. Only sent when the registry configures it per model.
    extra_body merges arbitrary provider/model-specific request fields
    (e.g. {"reasoning_format": "deepseek"} or OpenRouter routing hints).
    verify_model=False relaxes the served-model check for hosted APIs that
    answer with a canonical/aliased model name (mismatch is logged, not fatal).
    priority (StudioForge D48: 1 chat / 2 agent / 3 background;
    anything else is a 400) tiers THIS request's admission and the JIT load it
    triggers, upwards only — the rig-integration contract
    says send it in every StudioForge body; None omits the key (other
    OpenAI-compatible backends don't know it).
    stall_s: raise StreamStalled when the server sends nothing for this long
    (a generous safety net for stuck connections — it never cuts a model
    that is still producing). max_wall_s: raise RowTimeout past this total.
    """
    body: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "seed": seed,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if priority is not None:
        body["priority"] = int(priority)
    if tools:
        body["tools"] = tools
    if response_format:
        body["response_format"] = response_format
    if reasoning_format:
        body["reasoning_format"] = reasoning_format
    if extra_body:
        for k, v in extra_body.items():
            body.setdefault(k, v)

    r = ChatResult()
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    raw_tool_deltas: list[dict] = []
    usage: dict = {}
    first_tok_t = last_tok_t = None
    t0 = time.perf_counter()

    read_timeout = min(x for x in (timeout, stall_s, max_wall_s) if x)
    try:
        with httpx.Client(timeout=httpx.Timeout(read_timeout, connect=15)) as http:
            hdrs = {"Content-Type": "application/json", **(headers or {})}
            if api_key:
                hdrs["Authorization"] = f"Bearer {api_key}"
            with http.stream(
                "POST", f"{base_url}/chat/completions", json=body,
                headers=hdrs,
            ) as resp:
                if resp.status_code >= 400:
                    err = resp.read().decode(errors="replace")[:4000]
                    if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
                        if any(m in err.lower() for m in _GENERATION_REJECT_MARKERS):
                            raise GenerationRejected(f"HTTP {resp.status_code}: {err[:500]}")
                        raise RequestRejected(resp.status_code, err[:500])
                    raise classify_server_error(resp.status_code, err,
                                                resp.headers.get("Retry-After"))
                buffer = ""
                saw_sse = False
                for raw_chunk in resp.iter_text():
                    if max_wall_s and time.perf_counter() - t0 > max_wall_s:
                        raise RowTimeout(f"request exceeded its {max_wall_s:.0f}s wall-clock ceiling")
                    buffer += raw_chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data: "):
                            continue
                        saw_sse = True
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            continue
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if data.get("error"):
                            raise classify_server_error(
                                None, f"server error: {json.dumps(data['error'])[:2000]}")
                        if data.get("model"):
                            r.served_model = data["model"]
                        if data.get("usage"):
                            usage = data["usage"]
                        now = time.perf_counter()
                        for choice in data.get("choices", []):
                            delta = choice.get("delta", {})
                            content = delta.get("content")
                            if content:
                                if first_tok_t is None:
                                    first_tok_t = now
                                text_parts.append(content)
                                last_tok_t = now
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                            if reasoning:
                                if first_tok_t is None:
                                    first_tok_t = now
                                reasoning_parts.append(reasoning)
                                last_tok_t = now
                            if delta.get("tool_calls"):
                                if first_tok_t is None:
                                    first_tok_t = now
                                last_tok_t = now
                                raw_tool_deltas.extend(delta["tool_calls"])
                            if choice.get("finish_reason"):
                                r.finish_reason = choice["finish_reason"]
                if not saw_sse:
                    # server answered 200 with a non-SSE body (error JSON)
                    raise TransportError("no SSE data in 200 response")
    except (WrongModelError, TransportError):
        raise  # RequestRejected is a TransportError
    except httpx.ReadTimeout as e:
        waited = time.perf_counter() - t0
        if max_wall_s and waited >= max_wall_s * 0.99:
            raise RowTimeout(f"request exceeded its {max_wall_s:.0f}s wall-clock ceiling") from e
        if stall_s:
            raise StreamStalled(f"no data from the server for {read_timeout:.0f}s "
                                f"(stalled after {waited:.0f}s)") from e
        raise TransportError(str(e) or "read timeout") from e
    except Exception as e:
        raise TransportError(str(e)) from e

    r.total_s = time.perf_counter() - t0
    # Strip thinking tags at the boundary — every grader, the judge and the
    # transcript consume response_text, so a leaked (think)/<think> block can
    # never corrupt tool-call JSON, instruct checks or code extraction.
    visible, inline_thinking = split_thinking("".join(text_parts))
    r.response_text = visible.strip()
    r.reasoning_text = "".join(reasoning_parts)
    if inline_thinking.strip() and not r.reasoning_text.strip():
        # reasoning sent inline in content (MiniMax-M3): it IS the reasoning
        # channel for overflow detection, recovery and the transcript
        r.reasoning_text = inline_thinking
    r.tool_calls = _merge_tool_call_deltas(raw_tool_deltas)
    r.ttft_s = (first_tok_t - t0) if first_tok_t else None

    r.prompt_tokens = usage.get("prompt_tokens")
    r.completion_tokens = usage.get("completion_tokens")
    r.reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if first_tok_t and last_tok_t and last_tok_t > first_tok_t:
        r.gen_s = last_tok_t - first_tok_t
        if r.completion_tokens and r.completion_tokens >= 2:
            # first token lands at first_tok_t, so gen_s covers the remaining n-1
            r.tok_per_s = (r.completion_tokens - 1) / r.gen_s
    if r.prompt_tokens and r.ttft_s and r.ttft_s > 0:
        # upper-bound approximation: TTFT = queueing + prompt processing
        r.prompt_tok_per_s = r.prompt_tokens / r.ttft_s

    # The load-bearing check: LM Studio serves 200s from the wrong model.
    # Fail CLOSED — if a real completion arrived but the server never told us
    # which model produced it, we cannot prove it was the requested one, and a
    # silent wrong-model run was the #1 defect of v1. Only enforce once we've
    # actually seen output (an empty stream is handled as a TransportError above).
    produced_output = bool(r.response_text or r.reasoning_text or r.tool_calls)
    if verify_model:
        if produced_output and not r.served_model:
            raise WrongModelError(
                f"server returned no model identifier for a completion of {model!r} "
                f"(cannot verify it was the requested model)")
        if r.served_model and r.served_model != model:
            raise WrongModelError(
                f"requested {model!r} but server answered from {r.served_model!r}")
    elif r.served_model and r.served_model != model:
        # hosted APIs alias ids (e.g. a dated snapshot) — record, don't fail
        log.debug("served model %r differs from requested %r (verify_model off)",
                  r.served_model, model)

    return r


def stream_chat_retried(base_url: str, api_key: str, model: str, messages: list[dict],
                        **kwargs) -> ChatResult:
    """Retry transport failures with backoff. WrongModelError is NOT retried
    here — the caller must fix server state (reload the model) first. A
    StudioForge ``priority_hold`` 503 is not a failure at all: it is waited out
    on its own budget (``PRIORITY_HOLD_WAIT_S``), and only a hold outlasting
    that budget is handed on as an error. ``VramExhausted`` (a genuinely full
    507 — no ``busy_models``, no retry hint) is likewise NOT retried: per the
    rig-integration contract, retrying unchanged cannot
    succeed — the caller must shorten the request or stand down."""
    last_err: Exception | None = None
    held_s = 0.0
    attempt = 0
    while attempt < MAX_TRANSPORT_RETRIES:
        attempt += 1
        try:
            return stream_chat(base_url, api_key, model, messages, **kwargs)
        except (WrongModelError, RequestRejected, GenerationRejected, VramExhausted,
                StreamStalled, RowTimeout):
            # retrying an identical bad request / bad generation / full VRAM
            # cannot succeed, and a stalled or over-budget row must not cost
            # the run its ceiling three times over
            raise
        except PriorityHold as e:
            # Somebody's chat/agent model is loading. Wait it out on the hold
            # budget WITHOUT spending a transport retry: this is the server
            # working as designed, not an error, and the run continues once the
            # load finishes. Only an implausibly long hold falls through to the
            # ordinary handling below.
            last_err = e
            hint = max(float(e.retry_after_s or PRIORITY_HOLD_POLL_S), 1.0)
            wait = min(hint, PRIORITY_HOLD_WAIT_S - held_s)
            if held_s >= PRIORITY_HOLD_WAIT_S or wait <= 0:
                log.error("priority hold by %s still standing after %.0fs — giving up",
                          e.holder(), held_s)
                raise
            log.info("priority hold by %s, waiting %.0fs (%.0fs of %.0fs budget spent)",
                     e.holder(), wait, held_s, PRIORITY_HOLD_WAIT_S)
            time.sleep(wait)
            held_s += wait
            attempt -= 1
        except TransportError as e:
            last_err = e
            if attempt < MAX_TRANSPORT_RETRIES:
                wait = RETRY_BACKOFF_S * (2 ** (attempt - 1))
                if isinstance(e, VramContention) and e.retry_after_s:
                    # the server said how long the busy resident needs
                    wait = max(wait, min(float(e.retry_after_s), 120.0))
                log.warning("transport retry %d/%d in %.1fs: %s",
                            attempt, MAX_TRANSPORT_RETRIES, wait, str(e)[:300])
                time.sleep(wait)
    assert last_err is not None
    raise last_err


def server_alive(base_url: str, api_key: str, headers: dict | None = None) -> bool:
    hdrs = dict(headers or {})
    if api_key:
        hdrs["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=10) as http:
            resp = http.get(f"{base_url}/models", headers=hdrs)
            return resp.status_code == 200
    except Exception:
        return False
