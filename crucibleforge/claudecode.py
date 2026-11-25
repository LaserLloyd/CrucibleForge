"""``type: claude_code`` provider: a model reached through the local ``claude``
CLI on the owner's Claude subscription (NOT the Anthropic API).

models.yaml::

    providers:
      claudecode: {type: claude_code}          # no base_url, no key
    models:
      - {name: claude-fable-5-2-cc, provider: claudecode, model_id: claude-fable-5-2}

One request = one fixed-argv subprocess (never a shell), prompt on stdin, run
in an empty temp directory ``<root>/<uuid>`` (default
``/var/tmp/crucibleforge-cc-tmp``; override with ``CRUCIBLEFORGE_CC_TMP``), the
directory removed afterwards::

    claude -p --model <id> --output-format json
           --system-prompt <case system prompt, or a neutral default>
           --tools ""                  no tools (the harness cannot add any)
           --strict-mcp-config         no MCP servers (none are passed)
           --setting-sources ""        no user/project/local settings: no
                                       hooks, no CLAUDE.md, no memory
           --disable-slash-commands    no skills / commands
           [--resume <session_id>]     later turns of a multi-turn case

``--bare`` is deliberately NOT used: it never reads the OAuth/keychain login,
which is the whole point here. Every ``ANTHROPIC_*`` variable is removed from
the child environment, so the subscription login is what answers; this module
never reads or sets ``ANTHROPIC_API_KEY``. Sessions are persisted (so a later
turn can ``--resume`` them) under the throw-away cwd's project entry.

Multi-turn: the runner re-sends the whole message list each turn. The session
id of every reply is remembered under a hash of the conversation so far; a
later request whose history matches is sent as ``--resume <id>`` with only the
new user message. A history that does not match (or a prefilled one) starts a
fresh session with the earlier turns rendered into the prompt.

Not settable through the CLI: temperature, top_p, seed, max_tokens, thinking.
Rows run under the harness's own sampling (``sampling: harness-default`` in
the meta) and are NOT comparable to API rows. Tool cases (``tools=``) are
refused: the provider has no tools. Cost is 0 (``basis: subscription``).
Account-level refusals (usage/rate limit, not logged in) raise
``RequestRejected(402)`` so the row is infrastructure (re-queued), never a 0.

Subscription protection (a long run must not drain the 5-hour window the
owner's working sessions share). All refusals are the same infra-class
``RequestRejected(402)`` (row pending / re-queued, never scored) and are raised
BEFORE the CLI is started:

* ``CF_CC_MAX_CALLS`` (default 200) and ``CF_CC_MAX_TOKENS`` (default
  3,000,000; input + output + cache read/write as the CLI reports them): a
  JSON counter file shared by every thread and process of the run (flock'd;
  path ``CF_CC_COUNTER``, default ``<results dir>/cc_counter.json``). The
  counter restarts when its first call is more than 5 h old (one window).
  Past a cap: one log line ``claude_code cap reached (calls N/N)``.
* ``CF_CC_WINDOW="01:00-06:00"`` (local time, may wrap midnight; unset = no
  window): outside it every call is refused.
* After 2 consecutive usage/rate-limit responses a process-wide stop flag is
  set and every later call is refused at once (no hammering). A successful
  call resets the streak.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .api import ChatResult, RequestRejected, TransportError

DEFAULT_SYSTEM = "You are a helpful assistant. Answer the user's message directly."
DEFAULT_TMP_ROOT = "/var/tmp/crucibleforge-cc-tmp"
SAMPLING_NOTE = "run via Claude Code (harness sampling)"

_ACCOUNT_RE = re.compile(
    r"usage limit|rate.?limit|limit reached|out of (?:credit|usage)|credit balance|"
    r"not logged in|please run /login|invalid api key|authentication|unauthori[sz]ed|quota",
    re.IGNORECASE)

_SESSIONS: dict[str, str] = {}
_LOCK = threading.Lock()
log = logging.getLogger("crucibleforge.claudecode")

DEFAULT_MAX_CALLS = 200
DEFAULT_MAX_TOKENS = 3_000_000
COUNTER_TTL_S = 5 * 3600
STOP_AFTER = 2
_STOP = {"streak": 0, "stopped": False}


def reset_guard() -> None:
    """Clear the process-wide stop flag (tests)."""
    with _LOCK:
        _STOP.update(streak=0, stopped=False)


def _refuse(msg: str):
    log.warning(msg)
    raise RequestRejected(402, f"claude_code usage limit guard: {msg}")


def in_window(spec: str | None, now: _dt.time) -> bool:
    if not spec or not spec.strip():
        return True
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", spec)
    if not m:
        raise RequestRejected(400, f"CF_CC_WINDOW must look like 01:00-06:00, got {spec!r}")
    a = _dt.time(int(m[1]), int(m[2]))
    b = _dt.time(int(m[3]), int(m[4]))
    return a <= now < b if a <= b else (now >= a or now < b)


def _counter_path() -> Path:
    env = os.environ.get("CF_CC_COUNTER")
    if env:
        return Path(env)
    from .config import results_dir
    return Path(results_dir()) / "cc_counter.json"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def _counter(update=None):
    """Read (and optionally update) the shared counter under an flock."""
    path = _counter_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        from .filelock import lock
        lock(fh)
        fh.seek(0)
        try:
            d = json.loads(fh.read() or "{}")
        except json.JSONDecodeError:
            d = {}
        if not isinstance(d, dict) or time.time() - float(d.get("started") or 0) > COUNTER_TTL_S:
            d = {"calls": 0, "tokens": 0, "started": time.time()}
        if update:
            update(d)
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(d))
            fh.flush()
        return d


def _guard() -> None:
    with _LOCK:
        if _STOP["stopped"]:
            _refuse("claude_code stopped: two consecutive usage/rate-limit responses; "
                    "no further calls this process")
    spec = os.environ.get("CF_CC_WINDOW")
    if not in_window(spec, _dt.datetime.now().time()):
        _refuse(f"claude_code outside CF_CC_WINDOW={spec}")
    max_calls = _env_int("CF_CC_MAX_CALLS", DEFAULT_MAX_CALLS)
    max_tokens = _env_int("CF_CC_MAX_TOKENS", DEFAULT_MAX_TOKENS)
    state = {}

    def reserve(d):
        state.update(d)
        if d["calls"] < max_calls and d["tokens"] < max_tokens:
            d["calls"] += 1
    _counter(reserve)
    if state["calls"] >= max_calls:
        _refuse(f"claude_code cap reached (calls {state['calls']}/{max_calls})")
    if state["tokens"] >= max_tokens:
        _refuse(f"claude_code cap reached (tokens {state['tokens']}/{max_tokens})")


def _note_limit(hit: bool) -> None:
    with _LOCK:
        _STOP["streak"] = _STOP["streak"] + 1 if hit else 0
        if _STOP["streak"] >= STOP_AFTER:
            _STOP["stopped"] = True


def _tmp_root() -> Path:
    return Path(os.environ.get("CRUCIBLEFORGE_CC_TMP") or DEFAULT_TMP_ROOT)


def _convo_key(model: str, messages: list[dict]) -> str:
    return hashlib.sha256(json.dumps([model, messages], sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


def _text(content) -> str:
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) if isinstance(p, dict) else str(p)
                       for p in content)
    return "" if content is None else str(content)


def child_env() -> dict:
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("ANTHROPIC_")}


#: the reasoning efforts the Claude Code CLI accepts (``--effort``)
EFFORTS = ("low", "medium", "high", "xhigh", "max")
#: the 4.5 default (lead 2026-11-14, on the owner's word): explicit, recorded on every row
DEFAULT_EFFORT = "high"
EFFORT_ENV = "CRUCIBLEFORGE_CC_EFFORT"
#: what a row made before the effort was passed is read as (the CLI's own default)
UNSET_EFFORT = "default"


def effort_for(entry: dict | None) -> str:
    """The effort a claude_code model runs at: its models-file ``effort:`` key, else
    ``$CRUCIBLEFORGE_CC_EFFORT``, else ``high``. An unknown value is refused (ConfigError)."""
    from .config import ConfigError
    v = str((entry or {}).get("effort") or os.environ.get(EFFORT_ENV) or DEFAULT_EFFORT).strip().lower()
    if v not in EFFORTS:
        raise ConfigError(f"claude_code effort {v!r}: one of {', '.join(EFFORTS)} "
                          f"(models file ``effort:``, ${EFFORT_ENV})")
    return v


def row_effort(row: dict | None) -> str:
    """A stored row's Claude Code effort; a row without the field (an API row, or a claude_code row
    made before the effort was passed) reads as ``default``: never pooled with an explicit one."""
    return str((row or {}).get("cc_effort") or UNSET_EFFORT)


def build_argv(exe: str, model: str, system: str, *, resume: str | None,
               effort: str | None = None) -> list[str]:
    argv = [exe, "-p", "--model", model, "--output-format", "json",
            "--system-prompt", system, "--tools", "", "--strict-mcp-config",
            "--setting-sources", "", "--disable-slash-commands"]
    if effort:
        argv += ["--effort", effort]
    if resume:
        argv += ["--resume", resume]
    return argv


def _split(messages: list[dict]):
    system = ""
    rest = []
    for m in messages:
        if m.get("role") == "system" and not rest:
            system = _text(m.get("content"))
        else:
            rest.append(m)
    return system or DEFAULT_SYSTEM, rest


def _render(rest: list[dict]) -> str:
    if len(rest) == 1:
        return _text(rest[0].get("content"))
    lines = ["Conversation so far:"]
    for m in rest[:-1]:
        lines.append(f"[{m.get('role')}]\n{_text(m.get('content'))}")
    lines.append("Reply to the last user message below as the assistant.")
    lines.append(f"[user]\n{_text(rest[-1].get('content'))}")
    return "\n\n".join(lines)


_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
           "tool_use": "tool_calls"}


def run_chat(model: str, messages: list[dict], *, timeout: float = 900.0,
             tools=None, executable: str = "claude", effort: str | None = None, **_ignored) -> ChatResult:
    if tools:
        raise RequestRejected(400, "claude_code provider has no tools: tool cases cannot run here")
    exe = shutil.which(executable) or executable
    _guard()
    system, rest = _split(messages)
    if not rest or rest[-1].get("role") != "user":
        raise RequestRejected(400, "claude_code: the last message must be a user message")
    resume = None
    if len(rest) > 1:
        with _LOCK:
            resume = _SESSIONS.get(_convo_key(model, messages[:-1]))
    prompt = _text(rest[-1].get("content")) if resume else _render(rest)
    argv = build_argv(exe, model, system, resume=resume, effort=effort)
    root = _tmp_root()
    root.mkdir(parents=True, exist_ok=True)
    cwd = root / uuid.uuid4().hex
    cwd.mkdir()
    t0 = time.monotonic()
    try:
        try:
            proc = subprocess.run(argv, input=prompt, capture_output=True, text=True,
                                  cwd=str(cwd), env=child_env(), timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise TransportError(f"claude_code: no answer within {timeout:.0f}s") from e
        except OSError as e:
            raise TransportError(f"claude_code: cannot run {exe!r}: {e}") from e
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
    total = time.monotonic() - t0
    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        data = {}
    if isinstance(data, list):  # some versions print the event array
        data = next((d for d in reversed(data) if isinstance(d, dict)
                     and d.get("type") == "result"), {})
    if not isinstance(data, dict):
        data = {}
    text = str(data.get("result") or "")
    if proc.returncode != 0 or data.get("is_error") or not data:
        msg = (text or (proc.stderr or "").strip() or out or "no output")[:400]
        if _ACCOUNT_RE.search(msg) or data.get("api_error_status") in (401, 402, 403, 429):
            _note_limit(True)
            raise RequestRejected(402, f"claude_code account refusal (usage/rate limit or "
                                       f"login): {msg}")
        raise TransportError(f"claude_code exit {proc.returncode}: {msg}")
    _note_limit(False)
    usage = data.get("usage") or {}
    inp = int(usage.get("input_tokens") or 0)
    cache_r = int(usage.get("cache_read_input_tokens") or 0)
    cache_w = int(usage.get("cache_creation_input_tokens") or 0)
    stop = str(data.get("stop_reason") or "end_turn")
    _counter(lambda d: d.__setitem__(
        "tokens", int(d.get("tokens") or 0) + inp + cache_r + cache_w
        + int(usage.get("output_tokens") or 0)))
    r = ChatResult(
        response_text=text,
        served_model=model,
        finish_reason=_FINISH.get(stop, stop),
        total_s=float(data.get("duration_ms") or 0) / 1000 or total,
        gen_s=float(data.get("duration_api_ms") or 0) / 1000 or None,
        prompt_tokens=inp + cache_r + cache_w,
        completion_tokens=int(usage.get("output_tokens") or 0),
    )
    # extras beyond ChatResult's own fields (input/cache split, billing basis)
    r.input_tokens = inp
    r.cache_read_tokens = cache_r
    r.cache_write_tokens = cache_w
    r.cost_basis = "subscription"
    sid = data.get("session_id")
    if sid:
        with _LOCK:
            _SESSIONS[_convo_key(model, messages + [{"role": "assistant", "content": text}])] = sid
    return r
