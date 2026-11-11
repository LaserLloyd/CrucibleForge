"""Process-wide spend accounting, a hard dollar cap and a live spend journal
(2026-11-11).

Every completion that goes through ``Provider.chat`` for a PRICED model is
priced from the usage the provider actually reported (prompt-cache split
included, ``providers.call_cost_usd``) and added to one accumulator for the
whole process.

- ``CF_SPEND_CAP_USD`` (float): once the accumulated cost reaches it, every
  further priced call is refused BEFORE it is sent with
  ``RequestRejected(402, "... spending limit ...")`` — the billing-refusal
  path (preflight.billing_rejection): the row is stored as an errored
  transport row (infra, pending, re-queued, never a 0), and three in a row
  stop the model run (runner.MAX_CONSECUTIVE_TRANSPORT_ERRORS). Calls already
  in flight when the cap is crossed still complete and are counted, so the
  final total can pass the cap by at most (concurrency) calls.
- ``CF_SPEND_ALLOW_CASES`` (comma list of case ids): a staged run. A priced
  call made for any other case is refused unsent the same way (billing path:
  that job stays pending). A call with no case context (preflight, conformance)
  is refused too while it is set.
- ``CF_SPEND_JOURNAL`` (path): one JSON line per priced call — ts, model,
  provider, label/case/repeat when known, the token split, the call's cost
  and the running total — plus one ``"refused": true`` line per call the cap
  stopped. This file is what an operator watches live (``tail -f``).

Unpriced (local / rig) calls are never counted, journalled or refused.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()
_STATE = {"total": 0.0, "calls": 0}
_CTX = threading.local()


def reset() -> None:
    """Zero the accumulator (tests; a fresh process starts at zero)."""
    with _LOCK:
        _STATE["total"] = 0.0
        _STATE["calls"] = 0


def total() -> float:
    with _LOCK:
        return _STATE["total"]


def cap() -> float | None:
    raw = os.environ.get("CF_SPEND_CAP_USD", "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        raise SystemExit(f"CF_SPEND_CAP_USD={raw!r} is not a number") from None
    return v if v >= 0 else None


def set_context(**kw) -> None:
    """Label / case id / repeat of the calls this thread makes next (the
    runner sets it per job; worker threads each have their own)."""
    _CTX.info = {k: v for k, v in kw.items() if v is not None}


def clear_context() -> None:
    _CTX.info = {}


def _context() -> dict:
    return dict(getattr(_CTX, "info", None) or {})


def _journal(rec: dict) -> None:
    path = os.environ.get("CF_SPEND_JOURNAL", "").strip()
    if not path:
        return
    line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def allowed_cases() -> set[str] | None:
    raw = os.environ.get("CF_SPEND_ALLOW_CASES", "").strip()
    return {x.strip() for x in raw.split(",") if x.strip()} or None


def check(model: str, provider: str | None = None) -> None:
    """Refuse the call (billing path) when the cap is reached, or when
    ``CF_SPEND_ALLOW_CASES`` is set and this job's case is not in it (a staged
    run: only the named cases may spend; the others are refused unsent and
    stay pending)."""
    allow = allowed_cases()
    if allow is not None:
        cid = _context().get("case_id")
        if cid not in allow:
            with _LOCK:
                _journal({"ts": _now(), "model": model, "provider": provider, **_context(),
                          "refused": True, "reason": "case not in CF_SPEND_ALLOW_CASES"})
            from .api import RequestRejected
            raise RequestRejected(
                402, f"crucibleforge spend gate: case {cid!r} is not in CF_SPEND_ALLOW_CASES "
                     f"- spending limit, call not sent")
    c = cap()
    if c is None:
        return
    with _LOCK:
        spent = _STATE["total"]
        if spent < c:
            return
        _journal({"ts": _now(), "model": model, "provider": provider, **_context(),
                  "refused": True, "total_usd": round(spent, 6), "cap_usd": c})
    from .api import RequestRejected
    raise RequestRejected(
        402, f"crucibleforge spend cap reached: ${spent:.4f} of ${c:.2f} "
             f"(CF_SPEND_CAP_USD) - spending limit, call not sent")


def record(model: str, provider: str | None, result, cost: float) -> float:
    """Add one priced call; returns the running total."""
    with _LOCK:
        _STATE["total"] += cost
        _STATE["calls"] += 1
        run = _STATE["total"]
        pt = result.prompt_tokens or 0
        cr = result.cache_read_tokens or 0
        cw = result.cache_write_tokens or 0
        _journal({"ts": _now(), "model": model, "provider": provider, **_context(),
                  "input_tokens": max(0, pt - cr - cw), "cache_write_tokens": cw,
                  "cache_read_tokens": cr, "prompt_tokens_total": pt,
                  "output_tokens": result.completion_tokens or 0,
                  "finish_reason": result.finish_reason,
                  "call_usd": round(cost, 6), "total_usd": round(run, 6),
                  "cap_usd": cap()})
    return run
