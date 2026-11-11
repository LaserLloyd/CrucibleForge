"""Native Anthropic Messages API client (provider ``type: anthropic``, 2026-11-11).

Why: Anthropic's OpenAI-compatible layer (``/v1/chat/completions``) does not
support prompt caching, so a paid agentic episode re-bills its whole growing
context on every turn. The native ``POST /v1/messages`` endpoint caches the
prefix: each turn reads the previous turns from cache at a fraction of the
input price.

The engine keeps speaking OpenAI-shaped messages everywhere. This module only
translates at the wire: OpenAI-shaped request -> Messages request, Messages
SSE stream -> the same ``api.ChatResult`` the rest of the engine reads, and
HTTP errors -> the same exception classes as ``api.stream_chat`` (a billing
400 is a ``RequestRejected`` whose body carries the credit wording, so
``preflight.billing_rejection`` sees it exactly as on the compat path).

Behaviour parity with the compat path (this is a benchmark: Claude rows that
ran through the compat layer must stay comparable with rows run here). Each
decision, and why:

P1 No ``thinking`` field is sent. Claude 5-series models think by default;
   the compat layer sent nothing either, so the model runs in its default
   mode on both paths.
P2 No ``temperature``, ``top_p``, ``top_k`` or ``seed``. The compat provider
   dropped ``top_p``/``seed`` (``drop_params``) and the "as deployed" regime
   sends no temperature; 4.7+ Claude models 400 on non-default samplers.
P3 Thinking is never replayed. The compat layer never returned thinking, so
   the harness never sent it back; here every ``thinking`` /
   ``redacted_thinking`` block of a response is dropped on receipt (not put
   into ``reasoning_text`` either — compat rows have none, and a non-empty
   reasoning channel would change the runner's overflow-recovery decisions).
   RISK: the Claude API docs say thinking blocks should be passed back
   unchanged within a conversation; if the API ever refuses a history
   without them, the 400 is raised as ``api.ProtocolRejected`` (an errored
   transport row: excluded, pending — never the model's score).
P4 Same ``max_tokens``, same tool definitions (the same ``items: {}``
   normalisation ``api.stream_chat`` applies, then name / description /
   ``parameters`` -> ``input_schema``), same prompt text byte-for-byte.
   ``tool_choice`` is never sent (forced tool choice is a 400 on Fable 5.x).
P5 System / developer messages are hoisted, in order, into the top-level
   ``system`` (one text block each) — the compat layer's documented mapping.
P6 Per-call ``extra_body`` (llama.cpp / OpenAI-only fields such as
   ``chat_template_kwargs`` from the thinking-off recovery rung,
   ``reasoning_format``, ``priority``) and ``response_format`` are ignored:
   the Messages API would 400 on them, and the compat layer ignored them.
P7 Stop reasons map to OpenAI finish reasons: end_turn / stop_sequence /
   pause_turn -> ``stop``, tool_use -> ``tool_calls``, max_tokens and
   model_context_window_exceeded -> ``length``, refusal ->
   ``content_filter`` (the value the engine already treats as "the provider
   blocked it", runner._sanity_check) with ``native_stop_reason`` kept.
P8 ``prompt_tokens`` is the TOTAL input (input + cache write + cache read),
   ``completion_tokens`` the billed output (thinking included, as on compat);
   the cache split rides in ``cache_read_tokens`` / ``cache_write_tokens``
   and is used for cost only. TTFT / tok/s are timed on visible output
   (text and tool-call deltas), as compat returned only those.
P9 Message-shape fixes the Messages API needs and the compat layer did on
   its side: tool results become ``tool_result`` blocks in a user message,
   consecutive same-role messages merge into ONE message (so consecutive tool
   results share one user turn), and an assistant message with neither text
   nor tool calls is dropped (the API refuses empty content). Text blocks
   that are empty or whitespace-only are omitted for the same reason.

Prompt caching: a top-level ``cache_control: {"type": "ephemeral"}``
(automatic caching: the breakpoint sits on the last cacheable block and moves
forward as the conversation grows) plus one explicit breakpoint on the last
system block, so tools + system get a stable read point of their own. The
body is serialised deterministically (compact separators, insertion order —
every dict is built in a fixed order from the engine's messages; keys are NOT
sorted, because sorting would reorder tool-schema properties against the
compat path), with no timestamps or ids in it, so turn N+1's tools, system
and earlier messages are byte-identical to turn N's and the cache hits.
The harness itself still rewrites old tool results under context pressure
(codeenv.compact) — that is the benchmark's own rule for every model and
simply restarts the cache from that point.
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from . import stopflag
from .api import (Aborted, ChatResult, GenerationRejected, ProtocolRejected, RequestRejected,
                  RowTimeout, StreamStalled, TransportError, WindowClosed, WrongModelError,
                  _GENERATION_REJECT_MARKERS, _with_array_items, classify_server_error)

log = logging.getLogger(__name__)

DEFAULT_VERSION = "2023-06-01"
CACHE_CONTROL = {"type": "ephemeral"}

_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "pause_turn": "stop",
           "tool_use": "tool_calls", "max_tokens": "length",
           "model_context_window_exceeded": "length", "refusal": "content_filter"}

# a 400 about the conversation's thinking blocks is the harness's request
# shape (P3), never the model's work
_PROTOCOL_400 = re.compile(r"thinking|signature|redacted_thinking", re.IGNORECASE)


# ----------------------------------------------------------- translation

def _text_of(content) -> str:
    """Text of an OpenAI message content (a string or a list of text parts)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "input_text"):
                out.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                out.append(part)
        return "".join(out)
    return str(content)


def _text_block(text: str) -> dict | None:
    return {"type": "text", "text": text} if text and text.strip() else None


def _tool_input(arguments) -> dict:
    """``input`` object of a tool_use block from an OpenAI ``arguments``
    string. A model's malformed / non-object arguments become ``{}`` — the
    API requires an object; grading reads the original call, not this."""
    if isinstance(arguments, dict):
        return arguments
    try:
        val = json.loads(arguments or "{}")
    except (TypeError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


def _tool_def(t: dict) -> dict:
    fn = t.get("function") if isinstance(t, dict) and t.get("type") == "function" else t
    fn = fn or {}
    out = {"name": fn.get("name") or "unnamed_tool"}
    if fn.get("description"):
        out["description"] = fn["description"]
    out["input_schema"] = fn.get("parameters") or {"type": "object", "properties": {}}
    return out


def translate_messages(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """(system blocks, Messages-API messages) for OpenAI-shaped ``messages``.
    Pure and deterministic: the same input always yields equal output."""
    system: list[dict] = []
    out: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)  # P9: merge same-role neighbours
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role = m.get("role")
        if role in ("system", "developer"):
            b = _text_block(_text_of(m.get("content")))
            if b:
                system.append(b)
        elif role == "user":
            b = _text_block(_text_of(m.get("content")))
            push("user", [b] if b else [])
        elif role == "assistant":
            blocks = []
            b = _text_block(_text_of(m.get("content")))
            if b:
                blocks.append(b)
            for tc in m.get("tool_calls") or []:
                fn = (tc or {}).get("function") or {}
                blocks.append({"type": "tool_use", "id": str((tc or {}).get("id") or ""),
                               "name": fn.get("name") or "unnamed_tool",
                               "input": _tool_input(fn.get("arguments"))})
            # P3: reasoning_content is never replayed; an empty turn is dropped
            push("assistant", blocks)
        elif role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": str(m.get("tool_call_id") or ""),
                           "content": _text_of(m.get("content"))}])
    return system, out


def build_request(model: str, messages: list[dict], *, max_tokens: int,
                  tools: list | None = None, stop=None) -> dict:
    """The Messages request body (P1-P6). Key order is fixed."""
    system, msgs = translate_messages(messages)
    body: dict = {"model": model, "max_tokens": int(max_tokens),
                  "cache_control": dict(CACHE_CONTROL)}
    if tools:
        body["tools"] = [_tool_def(t) for t in _with_array_items(tools)]
    if system:
        system[-1] = {**system[-1], "cache_control": dict(CACHE_CONTROL)}
        body["system"] = system
    body["messages"] = msgs
    if stop:
        body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    body["stream"] = True
    return body


def serialize(body: dict) -> bytes:
    """Deterministic wire bytes: compact, insertion order, UTF-8."""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def request_headers(api_key: str, headers: dict | None) -> dict:
    """x-api-key auth (never a Bearer header), the configured extras (e.g.
    ``anthropic-workspace-id``) and an ``anthropic-version`` default."""
    h = {"Content-Type": "application/json"}
    for k, v in (headers or {}).items():
        if k.lower() in ("authorization", "x-api-key", "content-type"):
            continue
        h[k] = v
    if not any(k.lower() == "anthropic-version" for k in h):
        h["anthropic-version"] = DEFAULT_VERSION
    if api_key:
        h["x-api-key"] = api_key
    return h


# ----------------------------------------------------------- streaming

def _raise_http(status: int, err: str, retry_after: str | None) -> None:
    if 400 <= status < 500 and status not in (408, 429):
        low = err.lower()
        if any(m in low for m in _GENERATION_REJECT_MARKERS):
            raise GenerationRejected(f"HTTP {status}: {err[:500]}")
        if status == 400 and _PROTOCOL_400.search(err) and "credit" not in low:
            raise ProtocolRejected(status, err[:500])
        raise RequestRejected(status, err[:500])
    raise classify_server_error(status, err, retry_after)


def stream_messages(base_url: str, api_key: str, model: str, messages: list[dict], *,
                    max_tokens: int, tools: list | None = None, headers: dict | None = None,
                    verify_model: bool = False, timeout: float = 900.0,
                    stall_s: float | None = None, max_wall_s: float | None = None,
                    deadline: float | None = None, stop=None) -> ChatResult:
    """One streamed Messages call -> ChatResult (see the module docstring)."""
    body = build_request(model, messages, max_tokens=max_tokens, tools=tools, stop=stop)
    payload = serialize(body)
    r = ChatResult()
    text_parts: list[str] = []
    blocks: dict[int, dict] = {}
    usage: dict = {}
    st = {"stop": None, "done": False, "sse": False}
    first_tok_t = last_tok_t = None
    t0 = time.perf_counter()

    if deadline and time.time() >= deadline:
        raise WindowClosed("the run's time window closed: request not started")
    window_s = (max(1.0, deadline - time.time()) + 1.0) if deadline else None
    read_timeout = min(x for x in (timeout, stall_s, max_wall_s, window_s) if x)

    def _event(data: dict) -> None:
        nonlocal first_tok_t, last_tok_t
        typ = data.get("type")
        now = time.perf_counter()
        if typ == "message_start":
            msg = data.get("message") or {}
            if msg.get("model"):
                r.served_model = msg["model"]
            usage.update({k: v for k, v in (msg.get("usage") or {}).items() if v is not None})
        elif typ == "content_block_start":
            cb = dict(data.get("content_block") or {})
            idx = int(data.get("index", len(blocks)))
            blocks[idx] = {"type": cb.get("type"), "id": cb.get("id"), "name": cb.get("name"),
                           "json": ""}
            if cb.get("type") == "text" and cb.get("text"):
                text_parts.append(cb["text"])
            if cb.get("type") in ("text", "tool_use"):
                first_tok_t = first_tok_t or now
                last_tok_t = now
        elif typ == "content_block_delta":
            delta = data.get("delta") or {}
            blk = blocks.get(int(data.get("index", -1)))
            dt = delta.get("type")
            if dt == "text_delta":
                text_parts.append(delta.get("text") or "")
            elif dt == "input_json_delta" and blk is not None:
                blk["json"] += delta.get("partial_json") or ""
            else:
                return  # thinking_delta / signature_delta: dropped (P3)
            first_tok_t = first_tok_t or now
            last_tok_t = now
        elif typ == "message_delta":
            d = data.get("delta") or {}
            if d.get("stop_reason"):
                st["stop"] = d["stop_reason"]
            usage.update({k: v for k, v in (data.get("usage") or {}).items() if v is not None})
        elif typ == "message_stop":
            st["done"] = True
        elif typ == "error":
            raise classify_server_error(
                None, f"server error: {json.dumps(data.get('error') or data)[:2000]}")

    try:
        with httpx.Client(timeout=httpx.Timeout(read_timeout, connect=15)) as http:
            with http.stream("POST", f"{base_url}/messages", content=payload,
                             headers=request_headers(api_key, headers)) as resp:
                if resp.status_code >= 400:
                    err = resp.read().decode(errors="replace")[:4000]
                    _raise_http(resp.status_code, err, resp.headers.get("Retry-After"))
                buffer = ""

                def _line(line: str) -> None:
                    line = line.strip()
                    if not line.startswith("data:"):
                        return
                    st["sse"] = True
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        return
                    if isinstance(data, dict):
                        _event(data)

                for raw in resp.iter_text():
                    if stopflag.ABORT.is_set():
                        raise Aborted("stopping: stream dropped")
                    if max_wall_s and time.perf_counter() - t0 > max_wall_s:
                        raise RowTimeout(f"request exceeded its {max_wall_s:.0f}s wall-clock ceiling")
                    if deadline and time.time() >= deadline:
                        raise WindowClosed("the run's time window closed: request dropped")
                    buffer += raw
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        _line(line)
                if buffer.strip():
                    _line(buffer)
                if not st["sse"]:
                    raise TransportError("no SSE data in 200 response")
                if not st["done"] and not st["stop"]:
                    raise TransportError("stream dropped before message_stop / stop_reason")
    except (WrongModelError, TransportError):
        raise
    except httpx.ReadTimeout as e:
        waited = time.perf_counter() - t0
        if deadline and time.time() >= deadline:
            raise WindowClosed("the run's time window closed: request dropped") from e
        if max_wall_s and waited >= max_wall_s * 0.99:
            raise RowTimeout(f"request exceeded its {max_wall_s:.0f}s wall-clock ceiling") from e
        if stall_s:
            raise StreamStalled(f"no data from the server for {read_timeout:.0f}s "
                                f"(stalled after {waited:.0f}s)") from e
        raise TransportError(str(e) or "read timeout") from e
    except Exception as e:
        raise TransportError(str(e)) from e

    r.total_s = time.perf_counter() - t0
    r.response_text = "".join(text_parts).strip()
    r.tool_calls = [{"id": b["id"], "name": b["name"] or "", "arguments": b["json"] or "{}"}
                    for _, b in sorted(blocks.items()) if b["type"] == "tool_use"]
    stop_reason = st["stop"]
    r.finish_reason = _FINISH.get(stop_reason, "stop" if stop_reason else None)
    if stop_reason and stop_reason not in ("end_turn", "tool_use", "max_tokens", "stop_sequence"):
        r.native_stop_reason = stop_reason
    r.ttft_s = (first_tok_t - t0) if first_tok_t else None

    uncached = usage.get("input_tokens")
    cw = usage.get("cache_creation_input_tokens")
    cr = usage.get("cache_read_input_tokens")
    if any(v is not None for v in (uncached, cw, cr)):
        r.prompt_tokens = int(uncached or 0) + int(cw or 0) + int(cr or 0)
        r.cache_write_tokens = int(cw or 0)
        r.cache_read_tokens = int(cr or 0)
    if usage.get("output_tokens") is not None:
        r.completion_tokens = int(usage["output_tokens"])
    if first_tok_t and last_tok_t and last_tok_t > first_tok_t:
        r.gen_s = last_tok_t - first_tok_t
        if r.completion_tokens and r.completion_tokens >= 2:
            r.tok_per_s = (r.completion_tokens - 1) / r.gen_s
    if r.prompt_tokens and r.ttft_s and r.ttft_s > 0:
        r.prompt_tok_per_s = r.prompt_tokens / r.ttft_s

    produced = bool(r.response_text or r.tool_calls)
    if verify_model:
        if produced and not r.served_model:
            raise WrongModelError(f"server returned no model identifier for a completion of {model!r}")
        if r.served_model and r.served_model != model:
            raise WrongModelError(f"requested {model!r} but server answered from {r.served_model!r}")
    return r
