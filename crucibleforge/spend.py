"""Spend cap inside the API client (Fable planning review §3.10, 2026-11-07).

The v1 frontier-pack cycle enforced its $15 cap in one script; three hosted
calls were billed after the agent that owned the cap had died, because the
thread pool outlived it. The cap now lives where every hosted request passes:
``Provider.chat`` asks :func:`guard` before it sends and calls :func:`record`
with the result.

    CF_SPEND_CAP_USD=5        # or `run|all|judge|bench-all --spend-cap-usd 5`
    CF_SPEND_LEDGER=/path     # optional: one ledger shared by several processes

* The cap is the summed ``cost_usd`` of this process's hosted calls; with a
  ledger it is the sum of every line in the ledger (all processes that share
  it, this one included). Each priced call appends one JSON line.
* Once the sum reaches the cap, every further hosted call raises
  :class:`SpendCapExceeded` BEFORE any byte is sent. It is a
  ``WindowClosed``: never retried, no row written, the job stays pending for
  the next run (exactly like ``bench-all --until``).
* Local providers (StudioForge, llama.cpp, LM Studio, Ollama, vLLM) are free
  and never refused. A hosted model with no ``price:`` costs an unknown
  amount: it is let through and logged once, since the runner already refuses
  unpriced hosted models without ``--allow-unpriced``.
* A call already in flight when the cap is crossed finishes (its cost is
  recorded). Every hosted call RESERVES the largest single-call cost seen so
  far in one process-wide, lock-protected counter before it is sent, and the
  guard counts those reservations: workers x windows x samples cannot all
  slip past one stale total. The overshoot is bounded by one call's cost
  above the reservation per worker (before any call has finished the
  reservation is 0, so the first wave is at most one call per worker).
* A billed attempt that ends in an error (stall, timeout, rejected
  generation, a 5xx after the request went out) is charged too: from the
  token counts the error carries, else the reservation estimate. The guard
  runs again before every transport retry.
* 4.5 additions kept on top of this accounting: ``CF_SPEND_JOURNAL`` (one JSON
  line per priced call and per refusal, token/cache split included),
  ``CF_SPEND_ALLOW_CASES`` (a staged run: a priced call for any other case is
  refused unsent, ``RequestRejected`` 402, that job stays pending), the
  per-job context (``set_context``) and the prompt-cache routing key
  (``episode_key``). Unpriced models are never capped, counted or journalled
  (the caller, ``Provider.chat``, only reaches this module for a model with a
  ``price:`` block). A cap refusal is a ``SpendCapExceeded``: a WindowClosed
  (the run stops, no row, job pending) that is also a 402 ``RequestRejected``
  so 4.5 callers that catch the billing path still see it.
* ``DEFAULT_STAGED_CAP_USD`` applies ONLY to staged (--include-staged) runs
  that set no cap; live-board runs are unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from datetime import datetime, timezone

from .api import RequestRejected, WindowClosed

log = logging.getLogger(__name__)

CAP_ENV = "CF_SPEND_CAP_USD"
#: the cap of THIS invocation only (``--spend-cap-usd``): counts this process's own hosted spend and never reads the
#: shared ledger; the env cap with a ledger is the cumulative budget across invocations; both may apply
RUN_CAP_ENV = "CF_SPEND_RUN_CAP_USD"
LEDGER_ENV = "CF_SPEND_LEDGER"
#: a per-model cap: only this model's ledger lines count toward CF_SPEND_CAP_USD
#: (the one bench45 ledger is shared by every model; each has its own cap)
CAP_MODEL_ENV = "CF_SPEND_CAP_MODEL"
#: the global cap over EVERYTHING booked outside this ledger too (A8, 2026-11-12):
#: baseline + this ledger + helper journal + rerun journals (see :func:`status`)
GLOBAL_CAP_ENV = "CF_SPEND_GLOBAL_CAP_USD"
BASELINE_ENV = "CF_SPEND_BASELINE_USD"         # the earlier global ledger (11-09: 231.07)
HELPER_JOURNAL_ENV = "CF_SPEND_HELPER_JOURNAL"  # the helper's llmcall journal
RERUN_DIR_ENV = "CF_SPEND_RERUN_DIR"           # durable copies of the Claude rerun journals
#: where the 4.5 build keeps them (llmcall's JOURNAL = tools/spend.jsonl); used under
#: bench45 when the env var is unset, and a no-op wherever the path does not exist
_MASTER_PLAN = Path("~/Documents/Research/master-plan-20261111")
DEFAULT_HELPER_JOURNAL = _MASTER_PLAN / "tools" / "spend.jsonl"
DEFAULT_RERUN_DIR = _MASTER_PLAN / "build" / "spend-journals"
#: THE bench45 ledger: a fixed path, so every process and every model books to one file
BENCH45_LEDGER = Path("~/.local/share/crucibleforge/bench45-spend-ledger.jsonl")
BENCH45_PROFILE = "bench45"
DEFAULT_GLOBAL_CAP_USD = 330.0                  # the owner 2026-11-12 (was 260)
#: the earlier global ledger (11-09), the baseline under bench45 unless $CF_SPEND_BASELINE_USD
DEFAULT_BASELINE_USD = 231.07
#: provider types that run on hardware we own (no bill)
LOCAL_TYPES = {"studioforge", "lmstudio", "llamacpp", "llama.cpp", "ollama", "vllm", "local"}

_lock = threading.Lock()
_spent = [0.0]
_inflight = [0.0]          # sum of the reservations of calls on the wire
_max_call = [0.0]          # the largest single-call cost seen: the reservation size
# The judge is not part of any model's cost (owner 2026-11-24). A hosted judge's calls are booked apart: never in a
# model's Run cost, never against --spend-cap-usd / $CF_SPEND_CAP_USD; ledger lines of kind "judge" that those sums
# skip. The global runaway ceiling ($CF_SPEND_GLOBAL_CAP_USD / the bench45 default) still counts them: it bounds
# every dollar the ledger sees.
# Judge spend is decided by the ROLE of the call, never by model id (review F3): the hosted judge is served through
# its own provider entry (``<provider>~judge``, cli._guard_45_judge), marked here; a call made through it runs inside
# :func:`judge_role` (Provider.chat), so the same hosted model used as the model under test is still that model's
# spend (Run cost, --spend-cap-usd).
_JUDGE_PROVIDERS: set[str] = set()
_ROLE = threading.local()
_judge_spent = [0.0]
_judge_calls = [0]
_judge_tokens = [0, 0]             # (input, output) of every hosted judge call, priced or not
_warned: set[str] = set()

#: the cap a staged run gets when it sets none (USD)
DEFAULT_STAGED_CAP_USD = 15.0


class SpendCapExceeded(WindowClosed, RequestRejected):
    """The spend cap is reached: no further hosted call is sent. A
    WindowClosed (runner: stop, no row, job pending) and, for 4.5 callers on
    the billing path, an HTTP-402 RequestRejected."""

    def __init__(self, msg: str, *, spent: float | None = None, cap: float | None = None,
                 cap_name: str = "cap"):
        RequestRejected.__init__(self, 402, msg)
        self.args = (msg,)
        # the figures for the run's stop line (runner.cap_stop_reason); None = a gate with no figure
        self.spent, self.cap, self.cap_name = spent, cap, cap_name

    def __str__(self) -> str:
        return self.body


_CTX = threading.local()


def set_context(**kw) -> None:
    """Label / case id / repeat of the calls this thread makes next (the
    runner sets it per job; worker threads each have their own)."""
    _CTX.info = {k: v for k, v in kw.items() if v is not None}


def clear_context() -> None:
    _CTX.info = {}


def _context() -> dict:
    return dict(getattr(_CTX, "info", None) or {})


def episode_key(model: str) -> str | None:
    """A stable prompt-cache routing key for the job this thread is running:
    one per (label, model, case, repeat), so every turn of an agentic episode
    reaches the same cache and no two episodes share one. None outside a job."""
    import hashlib
    c = _context()
    if not c.get("case_id"):
        return None
    raw = f"{c.get('label')}|{model}|{c.get('case_id')}|{c.get('repeat')}"
    return "cf-" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def call_key() -> str:
    """A routing key for ONE request (providers.cache_route_scope_for "call"): never reused, so the
    provider holds no server-side state that the next request could pick up."""
    import uuid
    return "cf-" + uuid.uuid4().hex


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _journal(rec: dict) -> None:
    path = os.environ.get("CF_SPEND_JOURNAL", "").strip()
    if not path:
        return
    line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


def allowed_cases() -> set[str] | None:
    raw = os.environ.get("CF_SPEND_ALLOW_CASES", "").strip()
    return {x.strip() for x in raw.split(",") if x.strip()} or None


def check_allowed(model: str, provider: str | None = None) -> None:
    """``CF_SPEND_ALLOW_CASES`` set and this job's case not in it: refuse the
    call unsent (billing path; the job stays pending)."""
    allow = allowed_cases()
    if allow is None:
        return
    cid = _context().get("case_id")
    if cid not in allow:
        with _lock:
            _journal({"ts": _now(), "model": model, "provider": provider, **_context(),
                      "refused": True, "reason": "case not in CF_SPEND_ALLOW_CASES"})
        raise RequestRejected(
            402, f"crucibleforge spend gate: case {cid!r} is not in CF_SPEND_ALLOW_CASES "
                 f"- spending limit, call not sent")


JUDGE_PROVIDER_SUFFIX = "~judge"


def mark_judge(provider_name: str) -> None:
    """``provider_name`` serves this process's hosted judge: every call made through it is judge spend."""
    with _lock:
        _JUDGE_PROVIDERS.add(str(provider_name))


def forget_judges() -> None:
    """No hosted judge in this process any more (tests; a new invocation)."""
    with _lock:
        _JUDGE_PROVIDERS.clear()
        _judge_spent[0] = 0.0
        _judge_calls[0] = 0
        _judge_tokens[0] = _judge_tokens[1] = 0


class judge_role:
    """``with judge_role(provider.name):`` -- the calls made inside, on this thread, are judge calls when the provider
    is a marked judge provider (re-entrant)."""

    def __init__(self, provider_name: str | None):
        # a ``<provider>~judge`` entry is the judge's own by construction (config.provider_of resolves it)
        self.on = str(provider_name) in _JUDGE_PROVIDERS or str(provider_name).endswith(JUDGE_PROVIDER_SUFFIX)

    def __enter__(self):
        self.prev = getattr(_ROLE, "on", False)
        _ROLE.on = self.on or self.prev
        return self

    def __exit__(self, *exc):
        _ROLE.on = self.prev
        return False


def is_judge(model_id: str | None = None) -> bool:
    """True inside a judge call (:class:`judge_role`). ``model_id`` is ignored: the role decides, never the id."""
    return bool(getattr(_ROLE, "on", False))


def note_judge_call(result=None) -> None:
    """Count one hosted judge call and its tokens (priced or not: an unpriced judge is never invisible)."""
    if not is_judge():
        return
    with _lock:
        if result is None:
            _judge_calls[0] += 1
            return
        _judge_tokens[0] += int(getattr(result, "prompt_tokens", 0) or 0) + int(
            getattr(result, "billed_extra_prompt_tokens", 0) or 0)
        _judge_tokens[1] += int(getattr(result, "completion_tokens", 0) or 0) + int(
            getattr(result, "billed_extra_completion_tokens", 0) or 0)


def judge_tokens() -> tuple[int, int]:
    """(input, output) tokens of the hosted judge calls in this process."""
    with _lock:
        return _judge_tokens[0], _judge_tokens[1]


def judge_totals() -> tuple[int, float]:
    """(hosted judge calls sent, their USD) in this process."""
    with _lock:
        return _judge_calls[0], _judge_spent[0]


def check(model: str, provider: str | None = None) -> None:
    """The 4.5 admission hook ``Provider.chat`` calls before a priced call
    (the dollar cap itself is enforced by :func:`reserve` / :func:`guard`)."""
    check_allowed(model, provider)


def total() -> float:
    """This process's running total (the 4.5 name for ``spent`` without a ledger)."""
    with _lock:
        return _spent[0]


def reset() -> None:
    """Forget this process's running total (tests; a new CLI invocation)."""
    with _lock:
        _spent[0] = 0.0
        _inflight[0] = 0.0
        _max_call[0] = 0.0
        _warned.clear()
        _judge_spent[0] = 0.0
        _judge_calls[0] = 0


def cap() -> float | None:
    raw = os.environ.get(CAP_ENV, "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        log.error("%s=%r is not a number — every hosted call is refused until it is fixed",
                  CAP_ENV, raw)
        return 0.0                      # fail closed: a typo never means "no cap"
    return max(0.0, v)


def run_cap() -> float | None:
    """The cap of this invocation (``--spend-cap-usd``), or None."""
    raw = os.environ.get(RUN_CAP_ENV, "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        log.error("%s=%r is not a number - every hosted call is refused until it is fixed", RUN_CAP_ENV, raw)
        return 0.0


def any_cap() -> bool:
    return cap() is not None or run_cap() is not None or global_cap() is not None


def budget_line() -> str | None:
    """One plan line with both figures, or None when neither cap is set:
    ``this run: up to $X; budget: $Y spent of $Z`` (the budget is the env cap, summed over a ledger)."""
    rc, bc = run_cap(), cap()
    if rc is None and bc is None:
        return None
    parts = []
    if rc is not None:
        parts.append(f"this run: up to ${rc:.2f}")
    if bc is not None:
        parts.append(f"budget: ${spent():.2f} spent of ${bc:.2f}"
                     + ("" if ledger_path() is not None else " (this process only: no ledger)"))
    return "  spend caps - " + "; ".join(parts)


def _run_over(model_id: str, s: float, est: float = 0.0) -> None:
    """Refuse a hosted call that would take THIS process's own spend (``s``: spent plus its other calls on the
    wire) over the invocation cap. Never reads the ledger."""
    rc = run_cap()
    if rc is None or not (s >= rc or s + est > rc):
        return
    msg = (f"crucibleforge spend cap reached: ${s:.4f} spent or reserved by this run"
           + (f" + ${est:.4f} for this call" if est else "")
           + f" of the ${rc:.2f} run cap (--spend-cap-usd) - spending limit, {model_id} call not sent")
    _journal({"ts": _now(), "model": model_id, **_context(), "refused": True,
              "total_usd": round(s, 6), "run_cap_usd": rc})
    raise SpendCapExceeded(msg, spent=s, cap=rc, cap_name="run cap")


def ledger_path() -> Path | None:
    """The shared ledger: ``$CF_SPEND_LEDGER`` when the variable is set (an empty
    value = no ledger), else the FIXED bench45 ledger while the active profile is
    bench45 (never under pytest, never for the 4.1 ``bench`` profile)."""
    if LEDGER_ENV in os.environ:
        raw = os.environ[LEDGER_ENV].strip()
        return Path(raw).expanduser() if raw else None
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    try:
        from .profiles import active_name
        if active_name() == BENCH45_PROFILE:
            return BENCH45_LEDGER.expanduser()
    except Exception:  # noqa: BLE001 - no profile machinery: no default ledger
        pass
    return None


def cap_model() -> str | None:
    return os.environ.get(CAP_MODEL_ENV, "").strip() or None


try:                                    # POSIX only; Windows runs without the lock
    import fcntl as _fcntl
except ImportError:                     # pragma: no cover
    _fcntl = None

#: what one call is assumed to cost when nothing better is known (a request
#: with no price block or size to estimate from): conservative, never 0
FALLBACK_ESTIMATE_USD = 0.05
#: tokens a reply is assumed to use when the request names no max_tokens
ASSUMED_OUTPUT_TOKENS = 8192


class Reservation(float):
    """A reservation (USD, the conservative estimate of one call). ``rid``
    names it in the shared ledger so another process sees it."""
    rid: str | None = None
    run: float = 0.0       # the part counted against this invocation's own cap (kept apart from the ledger)


def estimate_call_cost(price: dict | None, messages=None, max_tokens=None) -> float:
    """Conservative USD estimate of one request BEFORE it is sent: the prompt
    size (3 characters a token, rounded up) at the input price plus the whole
    output allowance at the output price. 0.0 when there is no price."""
    if not price:
        return 0.0
    try:
        pin, pout = float(price.get("input", 0)), float(price.get("output", 0))
    except (TypeError, ValueError, AttributeError):
        return 0.0
    chars = sum(len(str(m.get("content") or "")) for m in (messages or []) if isinstance(m, dict))
    pt = chars / 3.0 + 64
    ct = float(max_tokens or ASSUMED_OUTPUT_TOKENS)
    return (pt * pin + ct * pout) / 1_000_000


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError, TypeError, OSError):
        return True
    return True


class _Ledger:
    """The shared ledger opened under an exclusive ``fcntl`` lock: every
    process's admission check (spent + live reservations) and its own
    reservation line happen as ONE step, so two processes cannot both pass the
    same stale total."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = self.path.open("a+", encoding="utf-8")
        if _fcntl is not None:
            _fcntl.flock(self.f, _fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            self.f.flush()
            if _fcntl is not None:
                _fcntl.flock(self.f, _fcntl.LOCK_UN)
        finally:
            self.f.close()

    def state(self, model: str | None = None, include_judge: bool = False) -> tuple[float, dict[str, float]]:
        """(sum of billed cost_usd, {rid: usd} of live, unreleased reservations);
        ``model`` keeps only that model's lines (a per-model cap). Judge lines (kind "judge", judge
        reservations) count only with ``include_judge`` (the global ceiling)."""
        self.f.seek(0)
        total, held, pids = 0.0, {}, {}
        for line in self.f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            kind = d.get("kind")
            if not include_judge and (kind == "judge" or (kind == "reserve" and d.get("judge"))):
                continue
            if kind == "release":
                held.pop(d.get("id"), None)
            elif model and d.get("model") != model:
                continue
            elif kind == "reserve":
                held[d.get("id")] = float(d.get("usd") or 0.0)
                pids[d.get("id")] = d.get("pid")
            else:
                try:
                    total += float(d.get("cost_usd") or 0.0)
                except (ValueError, TypeError):
                    pass
        return total, {k: v for k, v in held.items() if _pid_alive(pids.get(k))}

    def append(self, obj: dict) -> None:
        self.f.write(json.dumps(obj) + "\n")


def _ledger_sum(p: Path, model: str | None = None) -> float:
    try:
        with _Ledger(p) as lg:
            return lg.state(model)[0]
    except FileNotFoundError:
        return 0.0


def spent() -> float:
    """USD spent so far: the shared ledger's sum when one is named, else this
    process's own running total."""
    p = ledger_path()
    if p is not None:
        return _ledger_sum(p, cap_model())
    with _lock:
        return _spent[0]


def spent_locked() -> float:
    """:func:`spent` for a caller already holding the process lock."""
    p = ledger_path()
    return _ledger_sum(p, cap_model()) if p is not None else _spent[0]


def is_hosted(provider_type: str | None) -> bool:
    return str(provider_type or "openai").lower() not in LOCAL_TYPES


def local_host(base_url: str | None) -> bool:
    """A server on this machine or the local network: loopback, a private address (10/8, 172.16/12, 192.168/16,
    IPv6 ULA / link-local), the carrier-grade range 100.64/10 (a tailnet), a ``.local`` / ``.lan`` / ``.internal``
    name or a single-label host. A public name or address is not local; no URL = not known to be local."""
    import ipaddress
    from urllib.parse import urlsplit
    raw = str(base_url or "").strip()
    if not raw:
        return False
    try:
        host = (urlsplit(raw if "//" in raw else "//" + raw).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local", ".lan", ".internal", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host                       # a single-label name resolves on the local network only
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return True
    return ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10")


def is_hosted_at(provider_type: str | None, base_url: str | None) -> bool:
    """:func:`is_hosted` with the server's address: an OpenAI-shaped provider whose ``base_url`` is a local server
    (:func:`local_host`) is local -- free, no judge cost, no hosted wording."""
    return is_hosted(provider_type) and not local_host(base_url)


def _over(c: float, model_id: str, s: float, est: float = 0.0) -> SpendCapExceeded:
    inc = f" + ${est:.4f} for this call" if est else ""
    _journal({"ts": _now(), "model": model_id, **_context(), "refused": True,
              "total_usd": round(s, 6), "cap_usd": c})
    return SpendCapExceeded(f"crucibleforge spend cap reached: ${s:.4f} spent or reserved{inc} of "
                            f"the ${c:.2f} cap ({CAP_ENV}) - spending limit, {model_id} call not sent",
                            spent=s, cap=c)


def _bench45_active() -> bool:
    try:
        from .profiles import active_name
        return active_name() == BENCH45_PROFILE
    except Exception:  # noqa: BLE001
        return False


def global_cap() -> float | None:
    """The global cap: ``$CF_SPEND_GLOBAL_CAP_USD`` (an override only), else, while
    bench45 is active, the profile's ``spend.global_cap_usd`` (A6's file; absent
    = DEFAULT_GLOBAL_CAP_USD, 330). None outside bench45 (4.1 unchanged)."""
    raw = os.environ.get(GLOBAL_CAP_ENV, "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            log.error("%s=%r is not a number - every hosted call is refused", GLOBAL_CAP_ENV, raw)
            return 0.0
    if not _bench45_active():
        return None
    try:
        from .profiles import load_profile
        v = (load_profile(BENCH45_PROFILE).get("spend") or {}).get("global_cap_usd")
        return max(0.0, float(v)) if v is not None else DEFAULT_GLOBAL_CAP_USD
    except Exception:  # noqa: BLE001 - unreadable profile: the default, never "no cap"
        return DEFAULT_GLOBAL_CAP_USD


def explicit_cap_set(model_id: str) -> bool:
    """A cap somebody set on purpose for this call: ``$CF_SPEND_CAP_USD`` / ``--spend-cap-usd`` (scoped by
    ``$CF_SPEND_CAP_MODEL``) or ``$CF_SPEND_GLOBAL_CAP_USD``. bench45's standing global cap (the profile
    default) is NOT one (fix round 2026-11-18, P4): it still bounds every priced call, but it never makes
    an unpriced hosted call fail in tests, dry plans or conformance probes. A real run of an unpriced
    hosted model is refused at the run entry (``cli.cmd_run``) with the missing price named."""
    return bool(os.environ.get(GLOBAL_CAP_ENV, "").strip()) or _model_cap(model_id) is not None


def _model_cap(model_id: str) -> float | None:
    """The per-run cap that applies to ``model_id``: ``$CF_SPEND_CAP_USD``, but when
    ``$CF_SPEND_CAP_MODEL`` names a model, only for that model's own calls (S10 review: a
    second model or a hosted judge in the same run is not measured against it; the global
    cap still is)."""
    c = cap()
    m = cap_model()
    if c is not None and m and model_id != m:
        return None
    return c


def _global_gate(lg: "_Ledger | None", model_id: str, est: float = 0.0,
                 own: float = 0.0) -> None:
    """Refuse a hosted call that would take the GLOBAL total (everything booked
    outside this ledger + the whole ledger + live reservations) over
    ``$CF_SPEND_GLOBAL_CAP_USD``. ``lg`` is already open (and locked); None = no
    ledger, and this process's own spend and in-flight reservations stand in for it
    (S10 review: the gate must hold without a ledger too). ``own`` = the caller's
    reservation, never counted against itself."""
    g = global_cap()
    if g is None:
        return
    outside = outside_total_usd()
    if lg is not None:
        sp, held = lg.state(None, include_judge=True)
        rid = getattr(own, "rid", None)
        s = outside + sp + sum(v for k, v in held.items() if k != rid)
    else:
        s = outside + _spent[0] + _judge_spent[0] + max(0.0, _inflight[0] - float(own))
    if s >= g or s + est > g:
        _journal({"ts": _now(), "model": model_id, **_context(), "refused": True,
                  "total_usd": round(s, 6), "global_cap_usd": g})
        raise SpendCapExceeded(
            f"crucibleforge global spend cap reached: ${s:.2f} booked or reserved"
            + (f" + ${est:.4f} for this call" if est else "")
            + f" of the ${g:.2f} global cap ({GLOBAL_CAP_ENV}) - spending limit, "
              f"{model_id} call not sent", spent=s, cap=g, cap_name="global cap")


def guard(provider_type: str | None, model_id: str, own: float = 0.0) -> None:
    """Raise SpendCapExceeded when a hosted call would start over the cap.
    Spent plus the other calls' reservations (all processes, with a ledger)
    counts; ``own`` = this call's reservation, so a retry does not count itself. A hosted judge call meets
    the global ceiling only."""
    if is_judge(model_id):
        if not is_hosted(provider_type) or global_cap() is None:
            return
        p = ledger_path()
        if p is not None:
            with _Ledger(p) as lg:
                _global_gate(lg, model_id, own=own)
        else:
            with _lock:
                _global_gate(None, model_id, own=own)
        return
    c = _model_cap(model_id)
    if not is_hosted(provider_type) or (c is None and run_cap() is None and global_cap() is None):
        return
    with _lock:
        _run_over(model_id, _spent[0] + max(0.0, _inflight[0] - float(getattr(own, "run", 0.0) or 0.0)))
    if c is None and global_cap() is None:
        return
    c = float("inf") if c is None else c
    p = ledger_path()
    if p is not None:
        with _Ledger(p) as lg:
            _global_gate(lg, model_id, own=own)
            sp, held = lg.state(cap_model())
        rid = getattr(own, "rid", None)
        pending = sum(v for k, v in held.items() if k != rid)
        s = sp + pending
    else:
        with _lock:
            _global_gate(None, model_id, own=own)
            pending = max(0.0, _inflight[0] - float(own))
        s = spent() + pending
    if s >= c:
        raise _over(c, model_id, s)


def reserve(provider_type: str | None, model_id: str, estimate: float = 0.0) -> Reservation:
    """Guard AND reserve in one atomic step (a process lock, plus the ledger's
    fcntl lock when a shared ledger is named, so concurrent workers and
    processes cannot all pass the same stale total). The reservation is the
    larger of ``estimate`` (see :func:`estimate_call_cost`) and the dearest
    call seen so far, never less than FALLBACK_ESTIMATE_USD: the first call of
    a run is not free. -> a :class:`Reservation` for :func:`release`. A hosted judge call reserves against
    the global ceiling only."""
    if is_judge(model_id):
        return _reserve_judge(provider_type, model_id, estimate)
    c = _model_cap(model_id)
    if not is_hosted(provider_type) or (c is None and run_cap() is None and global_cap() is None):
        return Reservation(0.0)
    c = float("inf") if c is None else c
    est = max(float(estimate or 0.0), _max_call[0]) or FALLBACK_ESTIMATE_USD
    p = ledger_path()
    with _lock:
        _run_over(model_id, _spent[0] + _inflight[0], est)
        run_held = est if run_cap() is not None else 0.0
        if p is not None:
            with _Ledger(p) as lg:
                sp, held = lg.state(cap_model())
                s = sp + sum(held.values())
                _global_gate(lg, model_id, est)
                if s + est > c:          # the INCOMING call counts (GPT 7)
                    raise _over(c, model_id, s, est)
                tok = Reservation(est)
                tok.rid = f"{os.getpid()}-{time.time_ns()}"
                lg.append({"kind": "reserve", "id": tok.rid, "pid": os.getpid(),
                           "usd": round(est, 6), "model": model_id})
            tok.run = run_held
            _inflight[0] += run_held
            return tok
        _global_gate(None, model_id, est)
        s = _spent[0] + _inflight[0]
        if s + est > c:
            raise _over(c, model_id, s, est)
        _inflight[0] += est
        tok = Reservation(est)
        tok.run = est if run_held else 0.0
        return tok


def _reserve_judge(provider_type: str | None, model_id: str, estimate: float) -> Reservation:
    if not is_hosted(provider_type) or global_cap() is None:
        return Reservation(0.0)
    est = max(float(estimate or 0.0), 0.0) or FALLBACK_ESTIMATE_USD
    p = ledger_path()
    with _lock:
        if p is not None:
            with _Ledger(p) as lg:
                _global_gate(lg, model_id, est)
                tok = Reservation(est)
                tok.rid = f"{os.getpid()}-{time.time_ns()}"
                lg.append({"kind": "reserve", "id": tok.rid, "pid": os.getpid(), "usd": round(est, 6),
                           "model": model_id, "judge": True})
            tok.run = 0.0
            return tok
        _global_gate(None, model_id, est)
        # no ledger: nothing is held in flight for the judge (the model's run cap must never see it)
        return Reservation(0.0)


def release(token: float) -> None:
    if not token:
        return
    p = ledger_path()
    rid = getattr(token, "rid", None)
    with _lock:
        if p is not None and rid:
            with _Ledger(p) as lg:
                lg.append({"kind": "release", "id": rid})
            _inflight[0] = max(0.0, _inflight[0] - float(getattr(token, "run", 0.0) or 0.0))
            return
        _inflight[0] = max(0.0, _inflight[0] - float(token))


def record_error(provider_type: str | None, model_id: str, price: dict | None,
                 exc: BaseException, token: float = 0.0) -> float | None:
    """Charge a billed attempt that raised: the usage the exception carries,
    else the reservation estimate (conservative, never 0 while a cap is set)."""
    if not is_hosted(provider_type) or not any_cap() or (is_judge(model_id) and not price):
        return None
    cost = call_cost(price, exc)
    if cost is None:
        cost = float(token) or FALLBACK_ESTIMATE_USD
    return _add(model_id, cost)


def call_cost(price: dict | None, result) -> float | None:
    """USD of one completed call from the model's ``price`` block (USD per
    1M input/output tokens) and the result's token counts (billed retries
    included). None when the model is unpriced or no usage came back."""
    if not price:
        return None
    billed = getattr(result, "provider_cost_usd", None)
    if isinstance(billed, (int, float)) and not isinstance(billed, bool) and not (
            getattr(result, "billed_extra_prompt_tokens", 0)
            or getattr(result, "billed_extra_completion_tokens", 0)):
        # the provider's own figure for this one call (xAI cost_in_usd_ticks): its
        # cache discount and long-prompt tier included, which list x tokens is not
        return float(billed)
    if hasattr(result, "cache_read_tokens"):
        # a ChatResult: priced with the prompt-cache split (4.5)
        from .providers import call_cost_usd
        return call_cost_usd({"price": price}, result)
    try:
        pin, pout = float(price.get("input", 0)), float(price.get("output", 0))
    except (TypeError, ValueError, AttributeError):
        return None
    pt = (getattr(result, "prompt_tokens", None) or 0) + (
        getattr(result, "billed_extra_prompt_tokens", 0) or 0)
    ct = (getattr(result, "completion_tokens", None) or 0) + (
        getattr(result, "billed_extra_completion_tokens", 0) or 0)
    if not pt and not ct:
        return None
    return (pt * pin + ct * pout) / 1_000_000


def record(provider_type: str | None, model_id: str, price: dict | None, result,
           estimate: float = 0.0) -> float | None:
    """Add one finished hosted call to the running total (and the ledger). A
    PRICED model whose reply carried no usage is charged at ``estimate``."""
    if not is_hosted(provider_type):
        return None
    cost = call_cost(price, result)
    if cost is None and is_judge(model_id):
        return None                     # an unpriced judge: "judge cost not priced", never charged
    if cost is None and any_cap():
        # a hosted success the cap cannot price (no price block, or no usage in
        # the reply) is charged at its reservation estimate: never free (GPT 7)
        if not price and model_id not in _warned:
            _warned.add(model_id)
            log.warning("spend cap: %s has no price — each call is charged at its "
                        "estimate (%s)", model_id, float(estimate) or FALLBACK_ESTIMATE_USD)
        cost = float(estimate) or FALLBACK_ESTIMATE_USD
    if cost is None:
        if any_cap() and model_id not in _warned:
            _warned.add(model_id)
            log.warning("spend cap: %s has no price (or reported no usage) — its calls are "
                        "not counted toward the cap", model_id)
        return None
    return _add(model_id, cost, result, provider_type)


def _add(model_id: str, cost: float, result=None, provider: str | None = None) -> float:
    judge = is_judge(model_id)
    with _lock:
        if judge:
            _judge_spent[0] += cost
        else:
            _spent[0] += cost
            _max_call[0] = max(_max_call[0], cost)
        rec = {"ts": _now(), "model": model_id, "provider": provider, **_context(),
               "call_usd": round(cost, 6), "total_usd": round(_spent[0], 6), "cap_usd": cap()}
        if judge:
            rec["kind"] = "judge"
        if result is not None and hasattr(result, "cache_read_tokens"):
            pt = result.prompt_tokens or 0
            cr = result.cache_read_tokens or 0
            cw = result.cache_write_tokens or 0
            rec.update(input_tokens=max(0, pt - cr - cw), cache_write_tokens=cw,
                       cache_read_tokens=cr, prompt_tokens_total=pt,
                       output_tokens=result.completion_tokens or 0,
                       finish_reason=result.finish_reason)
        _journal(rec)
        p = ledger_path()
        if p is not None:
            with _Ledger(p) as lg:
                lg.append({"ts": round(time.time(), 3), "pid": os.getpid(),
                           "model": model_id, "cost_usd": round(cost, 6), **({"kind": "judge"} if judge else {})})
    return cost


# ------------------------------------------------------------------ status (A8)
#: flat-rate plan models in the helper journal: booked at list price but not metered
PLAN_PREFIXES = ("grok", "minimax")
COUNT_PLAN_ENV = "CF_SPEND_COUNT_PLAN"          # 1 = count plan models in the total too


def _num(env: str) -> float:
    raw = os.environ.get(env, "").strip()
    if not raw and env == BASELINE_ENV and _bench45_active():
        return DEFAULT_BASELINE_USD
    try:
        return float(raw or 0.0)
    except ValueError:
        if env == BASELINE_ENV:
            # S10 review: an unreadable baseline must fail closed (count as over any cap),
            # never as $0, which would hide the earlier spend from the global gate
            log.error("%s=%r is not a number - the global cap refuses every hosted call", env, raw)
            return float("inf")
        return 0.0


def _line_cost(d: dict) -> float:
    """USD of one journal line in any of the three journal shapes: the ledger
    (``cost_usd``), the CF_SPEND_JOURNAL (``call_usd``) or the helper (``cost``).
    Reservations, releases and refusals cost nothing."""
    if d.get("refused") or d.get("kind") in ("reserve", "release"):
        return 0.0
    for k in ("call_usd", "cost_usd", "cost"):
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return 0.0


def sum_journal(path: Path | str) -> dict:
    """{'total', 'plan', 'metered', 'by_model', 'lines'} of one JSONL journal.
    A missing file is zero. ``plan`` = flat-rate models (Grok, MiniMax) at list price."""
    by: dict[str, float] = {}
    lines = 0
    p = Path(path).expanduser()
    if p.is_file():
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            c = _line_cost(d)
            lines += 1
            m = str(d.get("model") or "?")
            by[m] = by.get(m, 0.0) + c
    plan = sum(v for m, v in by.items() if m.lower().startswith(PLAN_PREFIXES))
    tot = sum(by.values())
    return {"path": str(p), "total": tot, "plan": plan, "metered": tot - plan,
            "by_model": {k: round(v, 6) for k, v in sorted(by.items())}, "lines": lines}


def _rerun_files(rerun_dir: Path | str | None) -> list[Path]:
    if not rerun_dir:
        return []
    d = Path(rerun_dir).expanduser()
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def _default(path: Path, exists) -> str | None:
    """``path`` while bench45 is active and it exists, else None (a public checkout)."""
    p = path.expanduser()
    return str(p) if _bench45_active() and exists(p) else None


def status(*, ledger: Path | str | None = None, helper: Path | str | None = None,
           rerun_dir: Path | str | None = None, baseline: float | None = None,
           cap_usd: float | None = None, count_plan: bool | None = None) -> dict:
    """The global spend picture: baseline (the earlier global ledger) + the bench
    ledger + the helper journal + the Claude rerun journals, against the global cap.
    ``count_plan`` False (default): plan models (Grok, MiniMax) are shown but not
    counted, as the helper's own cap treats them; True counts them at list price."""
    if ledger is None:
        lp = ledger_path()
        ledger = lp if lp is not None else BENCH45_LEDGER
    helper = helper if helper is not None else (os.environ.get(HELPER_JOURNAL_ENV) or _default(
        DEFAULT_HELPER_JOURNAL, Path.is_file))
    rerun_dir = rerun_dir if rerun_dir is not None else (os.environ.get(RERUN_DIR_ENV) or _default(
        DEFAULT_RERUN_DIR, Path.is_dir))
    base = _num(BASELINE_ENV) if baseline is None else float(baseline)
    gcap = (global_cap() if global_cap() is not None else DEFAULT_GLOBAL_CAP_USD) \
        if cap_usd is None else float(cap_usd)
    if count_plan is None:
        count_plan = os.environ.get(COUNT_PLAN_ENV, "").strip() in ("1", "true", "yes")
    parts = {"baseline": {"total": base, "plan": 0.0, "metered": base, "path": None},
             "bench_ledger": sum_journal(ledger)}
    if helper:
        parts["helper_journal"] = sum_journal(helper)
    for f in _rerun_files(rerun_dir):
        parts[f"rerun:{f.name}"] = sum_journal(f)
    key = "total" if count_plan else "metered"
    total = sum(v[key] for v in parts.values())
    plan_total = sum(v["plan"] for v in parts.values())
    return {"parts": parts, "cap_usd": gcap, "count_plan": bool(count_plan), "total_usd": round(total, 4),
            "plan_usd_not_counted": 0.0 if count_plan else round(plan_total, 4),
            "headroom_usd": round(gcap - total, 4), "over": total >= gcap}


def outside_total_usd() -> float:
    """Everything booked OUTSIDE the bench ledger (baseline + helper + reruns), as
    the global gate counts it."""
    st = status(ledger=os.devnull)
    return st["total_usd"]


def render_status(st: dict) -> str:
    L = [f"spend: global cap ${st['cap_usd']:.2f}  ({'plan models counted' if st['count_plan'] else 'plan models (Grok/MiniMax) not counted'})"]
    for name, v in st["parts"].items():
        shown = v["total"] if st["count_plan"] else v["metered"]
        extra = f"  (+${v['plan']:.2f} plan, not counted)" if v["plan"] and not st["count_plan"] else ""
        L.append(f"  {name:34} ${shown:9.2f}{extra}")
    L.append(f"  {'TOTAL':34} ${st['total_usd']:9.2f}")
    L.append(f"  {'HEADROOM':34} ${st['headroom_usd']:9.2f}" + ("   OVER THE CAP" if st["over"] else ""))
    return "\n".join(L)
