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

    def scoreable_text(self) -> str:
        """The model's effective answer. Thinking models sometimes emit the
        whole answer in the reasoning channel with empty content.
        Thinking tags are stripped from whichever channel is used."""
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
                verify_model: bool = True,
                timeout: float = 900.0) -> ChatResult:
    """One streaming chat completion with timing + real token usage.

    reasoning_format (llama.cpp server param, e.g. "deepseek") forces CoT
    extraction into the reasoning_content channel instead of inline thinking
    tags in content. Only sent when the registry configures it per model.
    extra_body merges arbitrary provider/model-specific request fields
    (e.g. {"reasoning_format": "deepseek"} or OpenRouter routing hints).
    verify_model=False relaxes the served-model check for hosted APIs that
    answer with a canonical/aliased model name (mismatch is logged, not fatal).
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

    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=15)) as http:
            hdrs = {"Content-Type": "application/json", **(headers or {})}
            if api_key:
                hdrs["Authorization"] = f"Bearer {api_key}"
            with http.stream(
                "POST", f"{base_url}/chat/completions", json=body,
                headers=hdrs,
            ) as resp:
                if resp.status_code >= 400:
                    err = resp.read().decode(errors="replace")[:500]
                    raise TransportError(f"HTTP {resp.status_code}: {err}")
                buffer = ""
                saw_sse = False
                for raw_chunk in resp.iter_text():
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
                            raise TransportError(f"server error: {json.dumps(data['error'])[:300]}")
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
        raise
    except Exception as e:
        raise TransportError(str(e)) from e

    r.total_s = time.perf_counter() - t0
    # Strip thinking tags at the boundary — every grader, the judge and the
    # transcript consume response_text, so a leaked (think)/<think> block can
    # never corrupt tool-call JSON, instruct checks or code extraction.
    r.response_text = strip_thinking_tags("".join(text_parts)).strip()
    r.reasoning_text = "".join(reasoning_parts)
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
    here — the caller must fix server state (reload the model) first."""
    last_err: Exception | None = None
    for attempt in range(1, MAX_TRANSPORT_RETRIES + 1):
        try:
            return stream_chat(base_url, api_key, model, messages, **kwargs)
        except WrongModelError:
            raise
        except TransportError as e:
            last_err = e
            if attempt < MAX_TRANSPORT_RETRIES:
                wait = RETRY_BACKOFF_S * (2 ** (attempt - 1))
                log.warning("transport retry %d/%d in %.1fs: %s",
                            attempt, MAX_TRANSPORT_RETRIES, wait, e)
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
