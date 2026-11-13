"""StudioForge model backend — a llama.cpp-based multi-model OpenAI-compatible HTTP server.

Facts encoded here (verified against StudioForge 0.2.0, 2026-08-22; re-verified
against ``1.26-09-04-3`` on 2026-09-06 during the WP-BENCH refactor — see
the WP-BENCH audit):
- Endpoint = ``providers.<name>.base_url`` (``.../v1``); the management API
  lives beside it at ``.../api/*``. Mutating management routes (load, unload,
  leases, settings) require ``X-MCP-Pin`` from a remote caller — pass it via
  ``providers.<name>.headers`` (``${ENV}`` placeholders are expanded).
- GET /v1/models lists every downloaded model by its full id
  (publisher/repo/file-stem) with ``studioforge.state`` loaded / not-loaded.
- GET /api/status is the truth about residents: ``loaded[]`` rows carry
  ``state`` (loading/ready), ``active_requests``, ``loaded_by``, ``ttl_s`` and
  the live ``plan`` (devices, ctx_size, parallel, kv type). There is NO
  top-level ``busy`` block over REST (that exists only in the MCP
  ``server_status`` tool) — busy-ness is derived from ``loaded[]``.
- POST /api/models/{id}/load-recommended loads at exactly ``ctx_size`` per
  slot, evicting only IDLE residents. A window that does not fit is a
  structured 507 with per-mode suggestions and ``retry_after_s`` when the
  cause is a model that is serving right now.
- POST /api/leases gives CUDA devices to a holder: the named models are
  loaded onto exactly those cards, nothing else is planned there until the
  lease is released (DELETE) or idles out (``idle_ttl_s``; ``touch`` resets
  it). A resident mid-request makes the call a 503 + ``retry_after_s``.
  ``force=true`` can evict an IDLE resident regardless of dialect (a plain
  pin, or a D46 priority tier) — the server's own D46 refusal literally says
  "Pass force=true to evict them anyway". Because of that, this client never
  sets it on its own initiative on any dialect; see ``acquire_lease``. Leases
  do not govern foreign VRAM holders (ComfyUI) — see GET /api/vram/holders.
- The served model id is echoed back in every completion, so the
  api.WrongModelError guard still applies unchanged.
- **D46 (priority tiers on a lease claim, live since ~2026-08):** 1 = chat,
  2 = agent, 3 = background — this client's own leases are class 3
  unless ``lease_priority`` overrides it (unset today: see the WP-BENCH audit
  FIX-4, not yet implemented). A lease claim against a resident of a
  stronger-or-equal class is refused outright with a ``409`` (message
  containing ``higher-priority model`` / ``does not outrank`` / ``already
  leased``, or a structured ``lease_conflict`` code) — it is a **refusal**,
  not a queue position, and it carries no ``retry_after_s`` (a countdown makes
  no sense for "someone else currently outranks you"). ``acquire_lease``
  polls for this dialect on a fixed cadence instead of dying on the first
  attempt (WP-BENCH FIX-2). This module does not yet send ``priority`` or
  honour a ``vacate_url`` (D56, a polite ask a leaseholder can be told to
  stand down for) — those remain FIX-4, deliberately out of scope here.
- **D48 (``priority_hold``):** a ``503`` — distinct from the D46 ``409`` above
  — while a chat/agent-tier model is *loading* (not yet resident). Always
  transient, always carries ``retry_after_s``; see ``_refusal_reason``.
- **D55:** the server also guards a leased card against an unload issued by a
  caller that does not hold that lease. This client's own ``unload_all`` only
  ever targets IDLE residents visible via ``/api/status`` and has not needed
  changes for this; noted here so a future ``403``/``409`` on an unload is not
  mistaken for a regression in this file.

Rig etiquette baked in (2026-08-22 postmortem, extended 2026-09-06): never
evict a model that is serving a request — wait for it (bounded) instead;
never send ``force`` on our own initiative, on ANY refusal dialect — pinned
or D46-tiered — force eviction is a caller decision (WP-BENCH FIX-2, the
``--force-evict`` CLI flag), never an automatic escalation; never fall back
to a JIT load at planner defaults when the server said the requested window
does not fit; wait for ``state == ready`` before the first completion (a
warm-up sent during ``loading`` makes the server plan a second load that
then 507s).
"""
from __future__ import annotations

import logging
import re
import time
from json import loads as json_loads

import httpx

from . import stopflag

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:1234/v1"   # override in models.yaml providers.<name>.base_url
DEFAULT_API_KEY = ""

# A cold load of a big model can take 1–2 minutes; warm_model is allowed to
# block that long (matches the generous stream_chat timeout).
WARMUP_TIMEOUT_S = 900
# Engine readiness polling after an explicit load.
READY_POLL_S = 2.0
# A model that never shows up in /api/status after a successful load call is
# a failed load, not a slow one — give up after this long.
NEVER_APPEARED_GRACE_S = 60.0
# Transient management-API failures tolerated in a row while polling.
STATUS_ERROR_TOLERANCE = 3
# How long to wait for a resident that is mid-request before giving up on
# evicting it (a chat turn is seconds to a few minutes).
DEFAULT_WAIT_BUSY_S = 600.0
# Lease defaults: holder name the rig shows, and a safety TTL so a crashed
# client cannot hold the cards forever (the keepalive touches it meanwhile).
LEASE_HOLDER = "crucibleforge"
LEASE_IDLE_TTL_S = 7200.0


#: Connect timeout for every management call. The read timeout stays the
#: call's own (a cold load may legitimately answer after 15 minutes), but a
#: rig that does not even accept the connection (Tailscale key expired,
#: 2026-10-28) must fail in seconds, not hold a worker for the full 900 s.
MGMT_CONNECT_TIMEOUT_S = 10.0


def _timeout(total: float) -> httpx.Timeout:
    return httpx.Timeout(total, connect=min(float(total), MGMT_CONNECT_TIMEOUT_S))


class StudioForgeError(RuntimeError):
    """StudioForge could not serve the requested model."""

    def __init__(self, msg: str, *, status: int | None = None,
                 retry_after_s: float | None = None, suggestions=None):
        super().__init__(msg)
        self.status = status
        self.retry_after_s = retry_after_s
        self.suggestions = suggestions


class StatusUnavailable(StudioForgeError):
    """The management API itself could not be read (timeout, non-200) — a
    transient condition, distinct from "the model is not loaded"."""


# ------------------------------------------------------------------ plumbing

def _sleep(seconds: float) -> None:
    """A polling/back-off pause that ends the wait on a hard stop."""
    if stopflag.sleep(seconds, time.sleep):
        raise StudioForgeError("stopping (SIGTERM): wait abandoned")


def _api(base_url: str) -> str:
    """StudioForge's management API lives beside /v1 (…/api/*)."""
    return base_url[:-3] if base_url.endswith("/v1") else base_url


def _quote(model_id: str) -> str:
    from urllib.parse import quote
    return quote(model_id, safe="")


def _hdrs(api_key: str, headers: dict | None = None) -> dict:
    h = dict(headers or {})
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    # Rig-integration contract: an unnamed caller is
    # indistinguishable from an intruder in the rig's own rollup. Callers
    # normally already pass X-SF-Client via providers.Provider.mgmt_headers();
    # this is a fallback for any direct caller of this module.
    h.setdefault("X-SF-Client", LEASE_HOLDER)
    return h


def _retry_after(data, resp: httpx.Response | None) -> float | None:
    """``retry_after_s`` from a structured error body (several nestings the
    server uses) or a Retry-After header."""
    for holder in (data, (data or {}).get("error") if isinstance(data, dict) else None,
                   ((data or {}).get("error") or {}).get("studioforge")
                   if isinstance(data, dict) and isinstance(data.get("error"), dict) else None):
        if isinstance(holder, dict) and holder.get("retry_after_s") is not None:
            try:
                return float(holder["retry_after_s"])
            except (TypeError, ValueError):
                pass
    if resp is not None and resp.headers.get("Retry-After"):
        try:
            return float(resp.headers["Retry-After"])
        except ValueError:
            pass
    return None


def _suggestions(data):
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        sf = err.get("studioforge")
        if isinstance(sf, dict) and sf.get("suggestions") is not None:
            return sf["suggestions"]
        if err.get("suggestions") is not None:
            return err["suggestions"]
    return data.get("suggestions")


def _mgmt(method: str, base_url: str, api_key: str, headers: dict | None, path: str,
          json=None, timeout: float = 30.0) -> tuple[int, object]:
    """One management call. Returns (status, parsed body). Raises
    StatusUnavailable on a transport failure so callers can tell "the server
    did not answer" from "the server said no"."""
    try:
        with httpx.Client(timeout=_timeout(timeout)) as http:
            resp = http.request(method, f"{_api(base_url)}{path}", json=json,
                                headers=_hdrs(api_key, headers))
    except httpx.HTTPError as e:
        raise StatusUnavailable(f"StudioForge management API {method} {path}: {e}") from e
    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        data = {"raw": resp.text[:300]}
    if isinstance(data, dict):
        data = dict(data)
        data["_status"] = resp.status_code
        ra = _retry_after(data, resp)
        if ra is not None:
            data["_retry_after_s"] = ra
    return resp.status_code, data


# ---------------------------------------------------------------- discovery

def _models_json(base_url: str, api_key: str) -> list[dict]:
    """Raw entries from GET /v1/models (id, size_bytes, state, kind, ...)."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=_timeout(15)) as http:
            resp = http.get(f"{base_url}/models", headers=headers)
    except httpx.HTTPError as e:
        log.warning("StudioForge unreachable at %s: %s", base_url, e)
        return []
    if resp.status_code != 200:
        log.warning("StudioForge /models HTTP %d: %s",
                    resp.status_code, resp.text[:200])
        return []
    try:
        return list(resp.json().get("data", []))
    except ValueError:
        return []


def list_models_full(base_url: str = DEFAULT_BASE_URL,
                     api_key: str = DEFAULT_API_KEY) -> list[dict]:
    """Raw model records from GET /v1/models."""
    return _models_json(base_url, api_key)


def list_models(base_url: str = DEFAULT_BASE_URL,
                api_key: str = DEFAULT_API_KEY) -> list[str]:
    """Full model ids served by StudioForge (downloaded, loaded or not)."""
    return sorted(m["id"] for m in _models_json(base_url, api_key) if m.get("id"))


def is_available(model_id: str, base_url: str = DEFAULT_BASE_URL,
                 api_key: str = DEFAULT_API_KEY) -> bool:
    return model_id in list_models(base_url, api_key)


def loaded_models(base_url: str = DEFAULT_BASE_URL,
                  api_key: str = DEFAULT_API_KEY) -> list[dict]:
    """Models currently resident in VRAM (state == "loaded"). Informational:
    inference auto-loads regardless, so the bench never depends on this."""
    out = []
    for m in _models_json(base_url, api_key):
        if m.get("studioforge", {}).get("state") == "loaded":
            out.append({"identifier": m.get("id"), "model_key": m.get("id"),
                        "device": "studioforge"})
    return out


# ------------------------------------------------------------- status/plan

def status(base_url: str, api_key: str, headers: dict | None = None) -> dict:
    """GET /api/status. Raises StatusUnavailable when it cannot be read."""
    code, data = _mgmt("GET", base_url, api_key, headers, "/api/status", timeout=15)
    if code != 200 or not isinstance(data, dict):
        raise StatusUnavailable(f"GET /api/status HTTP {code}", status=code)
    return data


def residents(base_url: str, api_key: str, headers: dict | None = None) -> list[dict]:
    """Every resident model with the fields that matter for etiquette."""
    out = []
    for m in status(base_url, api_key, headers).get("loaded", []):
        plan = m.get("plan") or {}
        out.append({
            "model_id": m.get("model_id"), "state": m.get("state"),
            "active_requests": int(m.get("active_requests") or 0),
            "loaded_by": m.get("loaded_by"), "ttl_s": m.get("ttl_s"),
            "pinned": bool(m.get("pinned")),
            "plan": {"devices": plan.get("devices"), "ctx_size": plan.get("ctx_size"),
                     "parallel": plan.get("parallel"), "kv_cache_type": plan.get("kv_cache_type"),
                     "mode": plan.get("mode")},
        })
    return out


def busy_residents(base_url: str, api_key: str, headers: dict | None = None) -> list[dict]:
    """Residents that must NOT be evicted right now: mid-request or loading."""
    return [r for r in residents(base_url, api_key, headers)
            if r["active_requests"] > 0 or r["state"] == "loading"]


def loaded_plan(model_id: str, base_url: str, api_key: str,
                headers: dict | None = None) -> dict | None:
    """The server's live plan for a loaded model (state, parallel slots, ctx,
    devices), or None when a 200 status does not list it. Raises
    StatusUnavailable when the status endpoint cannot be read."""
    for m in status(base_url, api_key, headers).get("loaded", []):
        if m.get("model_id") == model_id:
            return {"state": m.get("state"), "active_requests": m.get("active_requests"),
                    "loaded_by": m.get("loaded_by"), **(m.get("plan") or {})}
    return None


def loaded_parallel(model_id: str, base_url: str, api_key: str,
                    headers: dict | None = None) -> int:
    """Parallel slots the server currently runs this model with (1 if unknown)."""
    try:
        live = loaded_plan(model_id, base_url, api_key, headers) or {}
        return max(1, int(live.get("parallel") or 1))
    except (StudioForgeError, TypeError, ValueError):
        return 1


def gpu_indices(base_url: str, api_key: str, headers: dict | None = None) -> list[int]:
    code, data = _mgmt("GET", base_url, api_key, headers, "/api/gpus", timeout=15)
    if code != 200 or not isinstance(data, dict):
        raise StatusUnavailable(f"GET /api/gpus HTTP {code}", status=code)
    return [int(g["index"]) for g in data.get("gpus", []) if "index" in g]


def wait_ready(model_id: str, base_url: str, api_key: str,
               timeout_s: float = WARMUP_TIMEOUT_S, poll_s: float = READY_POLL_S,
               headers: dict | None = None,
               never_appeared_grace_s: float = NEVER_APPEARED_GRACE_S) -> dict:
    """Block until GET /api/status reports ``model_id`` as ready; return its
    live plan.

    Raises StudioForgeError when: the model never appears within
    ``never_appeared_grace_s`` (the load call lied or the engine died before
    registering); it was seen and then a 200 status no longer lists it (load
    failed / evicted); the status API fails ``STATUS_ERROR_TOLERANCE`` times
    in a row; or the deadline passes. One transient status hiccup during a
    long load is tolerated and logged."""
    t0 = time.monotonic()
    deadline = t0 + timeout_s
    last = None
    errors = 0
    while True:
        try:
            live = loaded_plan(model_id, base_url, api_key, headers)
            errors = 0
        except StatusUnavailable as e:
            errors += 1
            log.warning("status poll failed while waiting for %s (%d/%d): %s",
                        model_id, errors, STATUS_ERROR_TOLERANCE, e)
            if errors >= STATUS_ERROR_TOLERANCE:
                raise
            live = last  # keep waiting on the last known state
        else:
            if live and live.get("state") == "ready":
                return live
            if live is None and last is not None:
                raise StudioForgeError(
                    f"{model_id} vanished from the loaded list while waiting for ready "
                    f"(last state {last.get('state')!r}) — load failed or it was evicted")
            if live is None and time.monotonic() - t0 >= never_appeared_grace_s:
                raise StudioForgeError(
                    f"{model_id} never appeared in /api/status within "
                    f"{never_appeared_grace_s:.0f}s of the load call — the load failed")
            if live and live.get("state") not in (None, "loading", "ready"):
                raise StudioForgeError(
                    f"{model_id} entered state {live.get('state')!r} while loading")
            last = live if live is not None else last
        if time.monotonic() >= deadline:
            raise StudioForgeError(
                f"{model_id} not ready after {timeout_s:.0f}s (state "
                f"{(live or {}).get('state')!r})")
        _sleep(poll_s)


# ------------------------------------------------------------------ loading

def recommended_load(model_id: str, base_url: str, api_key: str,
                     context_length: int | None = None,
                     headers: dict | None = None) -> dict | None:
    """Pick the server's best placement profile (GET /api/models/<id>/profiles)
    and return its load_args (including ``devices`` — a one-shot placement).
    The 0.2.0 shape nests the numbers under ``optimal``; the flat 0.1 shape
    is read as a fallback. Preference: profiles that fit, then the highest
    estimated single-stream gen tok/s, then the most parallel slots. Returns
    None when the endpoint is unavailable (older server)."""
    try:
        code, data = _mgmt("GET", base_url, api_key, headers,
                           f"/api/models/{_quote(model_id)}/profiles", timeout=30)
    except StatusUnavailable:
        return None
    if code != 200 or not isinstance(data, dict):
        return None

    def opt(p):
        return p.get("optimal") if isinstance(p.get("optimal"), dict) else p

    profs = [p for p in data.get("profiles", []) if (opt(p).get("fits") or p.get("fits"))]
    if not profs:
        return None
    profs.sort(key=lambda p: (-(opt(p).get("est_gen_tps") or 0),
                              -(opt(p).get("max_parallel") or 0)))
    best = profs[0]
    o = opt(best)
    args = dict(o.get("load_args") or best.get("load_args") or {})
    args.pop("model_id", None)
    if not args.get("parallel"):
        args["parallel"] = o.get("recommended_parallel") or o.get("max_parallel") or 1
    if not args.get("devices") and (o.get("devices") or best.get("devices")):
        args["devices"] = list(o.get("devices") or best.get("devices"))
    if context_length and args.get("ctx_size") and context_length < args["ctx_size"]:
        args["ctx_size"] = int(context_length)  # never ask for more than the registry wants
    args["_profile"] = {"mode": best.get("mode"), "est_gen_tps": o.get("est_gen_tps"),
                        "est_gen_tps_batched": o.get("est_gen_tps_batched"),
                        "vram_gib": o.get("vram_gib") or (o.get("vram_mb") or 0) / 1024 or None,
                        "recommended_parallel_basis": o.get("recommended_parallel_basis")}
    return args


def warm_model(model_id: str, base_url: str, api_key: str,
               headers: dict | None = None) -> float:
    """Prove the model can serve with a tiny completion; return seconds to
    first response. Raises StudioForgeError (carrying retry_after_s when the
    server gave one) if the model can't serve."""
    from .api import TransportError, VramContention, WrongModelError, stream_chat_retried
    t0 = time.perf_counter()
    try:
        stream_chat_retried(base_url, api_key, model_id,
                            [{"role": "user", "content": "Hi"}],
                            max_tokens=4, temperature=0.0, seed=42, headers=headers or None)
    except VramContention as e:
        raise StudioForgeError(f"StudioForge warm-up of {model_id} failed: {e}",
                               retry_after_s=e.retry_after_s, suggestions=e.suggestions) from e
    except (TransportError, WrongModelError) as e:
        raise StudioForgeError(f"StudioForge warm-up of {model_id} failed: {e}") from e
    return time.perf_counter() - t0


def load_recommended(model_id: str, base_url: str, api_key: str,
                     ctx_size: int, prefer_mode: str | None = None,
                     headers: dict | None = None, max_slots: int | None = None) -> dict | None:
    """StudioForge 0.2 ``POST /api/models/<id>/load-recommended``: the server
    picks placement, KV type and slot count for exactly ``ctx_size`` per slot
    (quality-first). Returns the response dict on success, ``{"_status": 507,
    "_retry_after_s": ..., "_suggestions": ...}`` when the window doesn't fit,
    or None when the endpoint doesn't exist (older server)."""
    body: dict = {"ctx_size": int(ctx_size)}
    if prefer_mode:
        body["prefer_mode"] = prefer_mode
    if max_slots:
        body["max_slots"] = int(max_slots)
    try:
        code, data = _mgmt("POST", base_url, api_key, headers,
                           f"/api/models/{_quote(model_id)}/load-recommended",
                           json=body, timeout=WARMUP_TIMEOUT_S)
    except StatusUnavailable as e:
        log.warning("load-recommended failed (%s)", e)
        return None
    if code in (404, 405):
        return None
    if not isinstance(data, dict):
        data = {"raw": data, "_status": code}
    if code >= 400:
        data["_status"] = code
        data["_suggestions"] = _suggestions(data)
    else:
        data.pop("_status", None)
    return data


_FEWER_SLOTS_RE = re.compile(r"reduce parallel from (\d+) to (\d+)", re.IGNORECASE)


def _fewer_slots_hint(res: dict) -> int | None:
    """The slot count a 507 ``insufficient_vram`` refusal says would fit
    ("reduce parallel from 8 to 7"), or None."""
    if res.get("_status") != 507:
        return None
    m = _FEWER_SLOTS_RE.search(json_dumps_safe(res))
    if not m:
        return None
    n = int(m.group(2))
    return n if n >= 1 else None


def json_dumps_safe(obj) -> str:
    try:
        import json
        return json.dumps(obj, default=str)
    except Exception:  # noqa: BLE001
        return str(obj)


def _refusal_reason(res: dict) -> str:
    """Why a structured refusal wants us to wait, for the log line. A
    ``priority_hold`` (StudioForge D46/D48) means a chat- or agent-tier model
    is LOADING and worse-tier loads and inference are held off until it serves
    — a different wait from the usual "a resident is mid-request", and worth
    saying out loud so a run that pauses for minutes is explicable."""
    err = res.get("error") if isinstance(res.get("error"), dict) else {}
    sf = err.get("studioforge") if isinstance(err.get("studioforge"), dict) else {}
    if "priority_hold" in (err.get("code"), res.get("code")):
        hold = sf.get("priority_hold") or (sf.get("busy") or {}).get("priority_hold") or {}
        who = hold.get("model_id") or "a better-tier model"
        tier = hold.get("priority")
        return f"priority hold by {who}" + (f" (tier {tier})" if tier else "")
    return "a resident is busy"


def _retry_wait(res: dict, waited: float, wait_busy_s: float) -> float | None:
    """Seconds to sleep before re-trying a structured refusal, or None when
    the refusal is final (no hint, or the wait budget is spent)."""
    ra = res.get("_retry_after_s")
    if ra is None or waited >= wait_busy_s:
        return None
    return max(2.0, min(float(ra), 60.0, wait_busy_s - waited))


# D46 tier-refusal dialect (RC-1): a lease claim against a
# resident of a stronger-or-equal priority class is refused with a 409 that
# carries none of these markers as a code, or the message itself: it is a
# "you do not outrank this" refusal, not "wait N seconds" — so it never
# carries retry_after_s and _retry_wait alone always says "final". Poll for
# it on a fixed cadence instead, bounded by the same wait_busy_s budget as
# every other lease wait.
_TIER_REFUSAL_CODE = "lease_conflict"
_TIER_REFUSAL_MARKERS = ("higher-priority model", "does not outrank", "already leased")
_TIER_REFUSAL_POLL_S = 60.0


def _tier_refusal_reason(code: int, res: dict, detail: str) -> str | None:
    """The specific reason text for a D46/lease_conflict retry, or ``None``
    when this 409 is neither — never for the sibling "pinned model(s) …
    pass force=true" dialect, which names a resident this client may not
    evict on its own (see ``acquire_lease``).

    Distinguishes (WP-BENCH review M3) a genuine D46 priority-tier refusal
    (the message markers below) from a plain structured ``lease_conflict``
    — observed in practice as another StudioForge CLIENT already holding
    one of these cards (e.g. ``lease 9f5... (render-tenant) holds CUDA [1]``),
    which is a co-tenant lease, not a priority tier. Checking the message
    markers FIRST matters: a future server could plausibly send
    ``code: lease_conflict`` on a genuine tier refusal too, and the message
    is the more specific signal when both are present. The audit's own
    failure-triage table depends on telling these apart from the log."""
    if code != 409:
        return None
    low = detail.lower()
    if any(marker in low for marker in _TIER_REFUSAL_MARKERS):
        return "a higher/equal-priority resident holds these cards (D46) — not a countdown, polling"
    err = res.get("error") if isinstance(res.get("error"), dict) else {}
    if str(res.get("code") or err.get("code") or "").lower() == _TIER_REFUSAL_CODE:
        return "another holder already leases one or more of these cards (lease_conflict) — polling"
    return None


_KV_Q8_OR_SMALLER = {"q8_0", "q5_1", "q5_0", "q4_1", "q4_0", "iq4_nl"}


def _try_widths(model_id: str, base_url: str, api_key: str, headers: dict | None,
                body_base: dict, widths: list[int], have: int) -> dict | None:
    """Reload ``model_id`` at each width (widest first) with ``body_base``;
    the first plan wider than ``have`` wins, else None."""
    kv = body_base.get("kv_cache_type") or "recommended"
    for n in widths:
        try:
            _mgmt("POST", base_url, api_key, headers,
                  f"/api/models/{_quote(model_id)}/unload", timeout=WARMUP_TIMEOUT_S)
            code, data = _mgmt("POST", base_url, api_key, headers,
                               f"/api/models/{_quote(model_id)}/load",
                               json={**body_base, "parallel": n}, timeout=WARMUP_TIMEOUT_S)
        except StatusUnavailable as e:
            log.warning("slot widening of %s to %d failed (%s)", model_id, n, e)
            continue
        if code >= 400:
            log.info("%s: %d slots at ctx %s (KV %s) refused (HTTP %s) — trying fewer",
                     model_id, n, body_base["ctx_size"], kv, code)
            continue
        try:
            wait_ready(model_id, base_url, api_key, headers=headers)
            warm_model(model_id, base_url, api_key, headers=headers)
        except StudioForgeError as e:
            log.info("%s: %d slots did not come up (%s) — trying fewer", model_id, n, e)
            continue
        new = loaded_plan(model_id, base_url, api_key, headers) or {}
        if int(new.get("parallel") or 1) > have:
            log.warning("%s: the rig recommended %d slot(s); widened to %s slots at ctx %s "
                        "(KV %s) on %s for the benchmark's concurrent jobs", model_id, have,
                        new.get("parallel"), new.get("ctx_size"), kv, new.get("devices"))
            return new
    return None


def _widen_slots(model_id: str, base_url: str, api_key: str, headers: dict | None,
                 live: dict, ctx: int, min_slots: int, target_slots: int) -> dict | None:
    """The rig's planner sizes slots for chat (one stream, its 'knee'); a
    benchmark sends ~30 independent jobs at once. When load-recommended
    lands below ``min_slots`` (2026-09-24: JoyFox 35B-A3B on two 5090s at
    parallel=1 — every profile was a 1-slot knee estimate at 262144 ctx),
    reload the SAME devices/ctx/KV with more slots, widest first. Each slot
    keeps the full ``ctx`` window, so no model is limited; a width the server
    refuses (507) just steps down. Returns the new plan, or None when nothing
    wider fit (the recommended load is restored)."""
    devices = live.get("devices")
    body_base = {"ctx_size": int(live.get("ctx_size") or ctx)}
    if devices:
        body_base["devices"] = list(devices)
    for k in ("kv_cache_type", "kv_cache_type_v"):
        if live.get(k):
            body_base[k] = live[k]
    have = int(live.get("parallel") or 1)
    widths = [n for n in (target_slots, 6, 4, 3, 2) if min(target_slots, 8) >= n > have]
    widths = sorted(set(widths), reverse=True)
    # Pass 1 keeps the recommended KV cache; pass 2 (2026-09-26) retries the
    # same widths with a q8_0 KV cache before settling for one slot. precog
    # 123B at f16 KV fitted one slot on all four cards — a 3x slower run —
    # while q8_0 (about half the KV memory, negligible quality cost) fitted 3.
    # Per load only: the model's saved settings are not touched.
    passes = [dict(body_base)]
    if str(live.get("kv_cache_type") or "f16").lower() not in _KV_Q8_OR_SMALLER:
        passes.append({**body_base, "kv_cache_type": "q8_0", "kv_cache_type_v": "q8_0"})
    for body in passes:
        new = _try_widths(model_id, base_url, api_key, headers, body, widths, have)
        if new is not None:
            return new
    log.warning("%s: could not widen beyond %d slot(s) — restoring the recommended load",
                model_id, have)
    try:
        load_recommended(model_id, base_url, api_key, ctx, headers=headers)
        wait_ready(model_id, base_url, api_key, headers=headers)
    except (StatusUnavailable, StudioForgeError) as e:
        log.warning("restoring the recommended load of %s failed: %s", model_id, e)
    return None


def load_model(model_id: str, base_url: str, api_key: str,
               context_length: int | None = None,
               recommended: bool = True, headers: dict | None = None,
               wait_busy_s: float = DEFAULT_WAIT_BUSY_S,
               min_slots: int = 0, target_slots: int = 8) -> float:
    """Load a StudioForge model at the registry context, the way the server
    recommends (placement + parallel slots), wait for it to be ready, then
    prove it serves. Returns load seconds.

    Refusals are honoured, not worked around: a 507 that names
    ``retry_after_s`` (a resident is mid-request) is waited out, bounded by
    ``wait_busy_s``; a 507 without one (the window genuinely does not fit)
    raises with the server's per-mode suggestions — the model is NOT
    JIT-loaded at planner defaults, because that would silently change the
    context/slots the results are labelled with. The placement-profile
    fallback is used only when load-recommended does not exist (older
    server); a plain JIT warm-up only when ``recommended`` is off."""
    t0 = time.perf_counter()
    wanted = int(context_length or 32768)
    if recommended:
        try:
            live = loaded_plan(model_id, base_url, api_key, headers)
        except StatusUnavailable:
            live = None
        if (live and live.get("state") == "ready" and int(live.get("parallel") or 1) > 1
                and int(live.get("ctx_size") or 0) >= wanted):
            log.info("%s already loaded with parallel=%s ctx=%s", model_id,
                     live.get("parallel"), live.get("ctx_size"))
            return 0.0
        if live is not None:
            # Resident but on a DEGENERATE placement (1 slot / short ctx —
            # e.g. a leftover JIT load on the slow cards). load-recommended
            # treats "already loaded at that ctx" as satisfied and returns
            # the existing plan unchanged, so the model must be unloaded
            # first for the server to re-plan it properly (2026-08-24: a
            # full run started serial on the 3090s exactly this way).
            log.info("%s resident on a degenerate placement (parallel=%s ctx=%s "
                     "devices=%s) — unloading so load-recommended re-plans it",
                     model_id, live.get("parallel"), live.get("ctx_size"),
                     live.get("devices"))
            try:
                _mgmt("POST", base_url, api_key, headers,
                      f"/api/models/{_quote(model_id)}/unload", timeout=WARMUP_TIMEOUT_S)
            except StatusUnavailable as e:
                log.warning("pre-replan unload failed (%s) — continuing", e)
        waited = 0.0
        max_slots = None
        while True:
            res = load_recommended(model_id, base_url, api_key, wanted, headers=headers,
                                   max_slots=max_slots)
            if res is None:
                break  # older server — profile fallback below
            if not res.get("_status"):
                plan = res.get("plan") or res
                log.info("loaded %s via load-recommended: ctx=%s parallel=%s mode=%s",
                         model_id, plan.get("ctx_size", wanted), plan.get("parallel"),
                         plan.get("mode") or plan.get("devices"))
                wait_ready(model_id, base_url, api_key, headers=headers)
                warm_model(model_id, base_url, api_key, headers=headers)
                live = loaded_plan(model_id, base_url, api_key, headers) or {}
                if min_slots and int(live.get("parallel") or 1) < min_slots:
                    live = _widen_slots(model_id, base_url, api_key, headers, live,
                                        wanted, min_slots, target_slots) or live
                log.info("%s serving: parallel=%s ctx=%s devices=%s", model_id,
                         live.get("parallel"), live.get("ctx_size"), live.get("devices"))
                return time.perf_counter() - t0
            detail = str(res.get("detail") or (res.get("error") or {}).get("message")
                         if isinstance(res.get("error"), dict) else res.get("error") or res)[:300]
            # 2026-09-25: the planner can recommend N slots and then refuse its
            # own load by a few GiB ("reduce parallel from 8 to 7"). Take the
            # server's own suggestion once per step instead of failing the load.
            fewer = _fewer_slots_hint(res)
            if fewer and (max_slots is None or fewer < max_slots):
                log.warning("load-recommended for %s at ctx=%d: the rig refused its own slot "
                            "count — retrying at max_slots=%d (%s)", model_id, wanted, fewer,
                            detail[:160])
                max_slots = fewer
                continue
            wait = _retry_wait(res, waited, wait_busy_s)
            if wait is not None:
                log.warning("load-recommended for %s at ctx=%d refused (HTTP %s, %s) — "
                            "retrying in %.0fs (%s)", model_id, wanted,
                            res.get("_status"), _refusal_reason(res), wait, detail)
                _sleep(wait)
                waited += wait
                continue
            raise StudioForgeError(
                f"load-recommended for {model_id} at ctx={wanted} refused (HTTP "
                f"{res.get('_status')}): {detail}", status=res.get("_status"),
                retry_after_s=res.get("_retry_after_s"), suggestions=res.get("_suggestions"))
        args = recommended_load(model_id, base_url, api_key, context_length, headers)
        if args:
            prof = args.pop("_profile", {})
            body = {k: v for k, v in args.items()
                    if k in ("ctx_size", "kv_cache_type", "parallel", "devices")}
            log.info("loading %s via recommended profile %s (parallel=%s, ctx=%s, devices=%s, "
                     "est %s tok/s single / %s batched)", model_id, prof.get("mode"),
                     body.get("parallel"), body.get("ctx_size"), body.get("devices"),
                     prof.get("est_gen_tps"), prof.get("est_gen_tps_batched"))
            try:
                code, data = _mgmt("POST", base_url, api_key, headers,
                                   f"/api/models/{_quote(model_id)}/load",
                                   json=body, timeout=WARMUP_TIMEOUT_S)
                if code >= 400:
                    raise StudioForgeError(
                        f"profile load of {model_id} rejected (HTTP {code}: "
                        f"{str(data)[:200]})", status=code,
                        retry_after_s=(data or {}).get("_retry_after_s")
                        if isinstance(data, dict) else None)
                wait_ready(model_id, base_url, api_key, headers=headers)
            except StatusUnavailable as e:
                raise StudioForgeError(f"profile load of {model_id} failed: {e}") from e
    warm_model(model_id, base_url, api_key, headers=headers)
    try:
        live = loaded_plan(model_id, base_url, api_key, headers) or {}
    except StatusUnavailable:
        live = {}
    log.info("%s serving: parallel=%s ctx=%s devices=%s", model_id,
             live.get("parallel"), live.get("ctx_size"), live.get("devices"))
    return time.perf_counter() - t0


# ------------------------------------------ wider placement (2026-11-13)
# Nanbeige4.3-33B Q8_0 (2026-11-13): full attention on every layer, ~113 KB
# of q8_0 KV per token. On the two 5090s at the run's 73728-token window the
# rig fitted 2 slots (3 was short by ~1.7 GB), so 32 thinking jobs queued two
# at a time: ~96 tok/s aggregate and a 4.7 h generation, while the two idle
# 3090s were never asked. When the preferred cards end up below ``min_slots``
# the provider now asks the rig's own dry-run planner (GET /plan, nothing is
# loaded) how many slots wider card sets would hold at the SAME ctx per slot,
# scores each as slots x the rig's per-stream speed estimate for that set, and
# moves only when the gain is clear (3090s are slower; a 4-card layer split
# runs each stream at ~60 % of the 5090-pair speed). Budgets, ctx and every
# scoring input are unchanged -- only placement and slot count move.

#: A wider placement must promise at least this much more aggregate
#: throughput (slots x per-stream tok/s) than the current one to be worth a
#: reload and holding extra cards for the run.
WIDEN_DEVICES_MIN_GAIN = 1.5


def placement_speeds(base_url: str, api_key: str, headers: dict | None,
                     model_id: str) -> dict[tuple[int, ...], float]:
    """The rig's single-stream generation estimate (``est_gen_tps``) per
    device set, from ``GET /api/models/{id}/options`` -- the ``placements``
    table plus the per-context ``options`` rows. Keys are sorted device
    tuples; the best estimate per set wins. Empty when unavailable."""
    try:
        code, data = _mgmt("GET", base_url, api_key, headers,
                           f"/api/models/{_quote(model_id)}/options", timeout=60)
    except StatusUnavailable as e:
        log.warning("placement speeds for %s unavailable: %s", model_id, e)
        return {}
    if code != 200 or not isinstance(data, dict):
        return {}
    model = data.get("model") if isinstance(data.get("model"), dict) else data
    rows = []
    for pl in model.get("placements") or []:
        opt = pl.get("optimal") if isinstance(pl.get("optimal"), dict) else {}
        rows.append((opt.get("devices") or pl.get("devices"), opt.get("est_gen_tps")))
    for o in model.get("options") or []:
        if isinstance(o, dict):
            rows.append((o.get("devices"), o.get("est_gen_tps")))
    out: dict[tuple[int, ...], float] = {}
    for devs, tps in rows:
        try:
            key = tuple(sorted(int(d) for d in devs or ()))
            val = float(tps)
        except (TypeError, ValueError):
            continue
        if key and val > 0:
            out[key] = max(out.get(key, 0.0), val)
    return out


def per_slot_speed(speeds: dict[tuple[int, ...], float], devices) -> float | None:
    """Per-stream tok/s estimate for ``devices``: the exact set's estimate,
    else the SLOWEST known superset's (more slow cards never make a layer
    split faster, so that is a pessimistic stand-in), else None."""
    key = tuple(sorted(int(d) for d in devices))
    if key in speeds:
        return speeds[key]
    sup = [v for k, v in speeds.items() if set(key) < set(k)]
    return min(sup) if sup else None


def own_vram_bytes(base_url: str, api_key: str, headers: dict | None,
                   model_id: str) -> dict[int, int]:
    """Bytes ``model_id``'s own engine holds per card right now (measured
    ``vram_processes``, else the live plan's estimate). A dry-run plan counts
    them as occupied; a reload frees them first."""
    st = status(base_url, api_key, headers)
    for p in st.get("vram_processes") or []:
        if isinstance(p, dict) and p.get("alias") == model_id and p.get("per_gpu_bytes"):
            try:
                return {int(k): int(v) for k, v in p["per_gpu_bytes"].items()}
            except (TypeError, ValueError, AttributeError):
                break
    for m in st.get("loaded") or []:
        if m.get("model_id") == model_id:
            try:
                return {int(k): int(v) for k, v in
                        ((m.get("plan") or {}).get("per_gpu_bytes") or {}).items()}
            except (TypeError, ValueError, AttributeError):
                return {}
    return {}


def dry_run_fits(base_url: str, api_key: str, headers: dict | None, model_id: str,
                 devices, ctx: int, kv: str, parallel: int, credit_bytes: int = 0) -> bool:
    """Would a load of ``model_id`` at exactly (devices, ctx per slot, KV,
    parallel) fit? ``GET /api/models/{id}/plan`` is the rig's dry run --
    nothing is loaded -- but it answers against the cards as they stand, so
    the bytes this model itself holds there (``credit_bytes``, freed by the
    reload) are credited against a capacity shortfall. Any other refusal
    (no shortfall figure) is a no."""
    from urllib.parse import urlencode
    q = [("ctx_size", int(ctx)), ("kv_cache_type", kv), ("kv_cache_type_v", kv),
         ("parallel", int(parallel))] + [("devices", int(d)) for d in devices]
    try:
        code, data = _mgmt("GET", base_url, api_key, headers,
                           f"/api/models/{_quote(model_id)}/plan?{urlencode(q)}", timeout=60)
    except StatusUnavailable:
        return False
    if code != 200 or not isinstance(data, dict):
        return False
    if data.get("fits"):
        return True
    short = data.get("shortfall_bytes")
    try:
        return short is not None and int(short) <= int(credit_bytes)
    except (TypeError, ValueError):
        return False


def max_slots_dry_run(base_url: str, api_key: str, headers: dict | None, model_id: str,
                      devices, ctx: int, kv: str, widths, credit_bytes: int = 0) -> int:
    """The widest of ``widths`` (tried widest first) the dry run accepts, else 0."""
    for n in sorted(set(int(w) for w in widths), reverse=True):
        if dry_run_fits(base_url, api_key, headers, model_id, devices, ctx, kv, n,
                        credit_bytes):
            return n
    return 0


def choose_wider_placement(current: dict, candidates: list[dict],
                           min_gain: float = WIDEN_DEVICES_MIN_GAIN,
                           cap_slots: int | None = None) -> dict | None:
    """Pure decision: the candidate with the best expected aggregate
    throughput (``slots`` x per-stream ``tps``), if it beats ``current`` by at
    least ``min_gain``; None keeps the current placement. Slots count only up
    to ``cap_slots`` (the run's ``target_slots``). Ties go to fewer cards
    (leave the rest of the rig free)."""
    def score(c):
        slots = int(c.get("slots") or 0)
        if cap_slots:
            slots = min(slots, int(cap_slots))
        return slots * float(c.get("tps") or 0.0)

    base = score(current)
    best = None
    for c in candidates:
        if not c.get("slots") or not c.get("tps"):
            continue
        if best is None or (score(c), -len(c["devices"])) > (score(best), -len(best["devices"])):
            best = c
    if best is None or base <= 0 or score(best) < base * float(min_gain):
        return None
    return {**best, "gain": score(best) / base}


# ---------------------------------------------------------------- unloading

def unload_all(base_url: str = DEFAULT_BASE_URL, api_key: str = DEFAULT_API_KEY,
               headers: dict | None = None, wait_busy_s: float = DEFAULT_WAIT_BUSY_S,
               keep: tuple[str, ...] = ()) -> int:
    """Unload every IDLE resident so the next load gets a clean slate.

    A resident that is serving a request (or still loading) is never
    unloaded from under its client: this waits — polling, bounded by
    ``wait_busy_s`` — for it to go idle first, and if it never does, raises
    StudioForgeError naming it. ``keep`` ids are left alone. Returns the
    number of models unloaded."""
    try:
        res = residents(base_url, api_key, headers)
    except StatusUnavailable as e:
        log.warning("cannot read residents (%s) — nothing unloaded", e)
        return 0
    targets = [r for r in res if r["model_id"] and r["model_id"] not in keep]
    if not targets:
        return 0
    t0 = time.monotonic()
    while True:
        busy = [r for r in targets if r["active_requests"] > 0 or r["state"] == "loading"]
        if not busy:
            break
        if time.monotonic() - t0 >= wait_busy_s:
            names = ", ".join(f"{r['model_id'].rsplit('/', 1)[-1]} ({r['active_requests']} "
                              f"active, {r['state']})" for r in busy)
            raise StudioForgeError(
                f"will not evict a serving model after waiting {wait_busy_s:.0f}s: {names}")
        log.info("waiting for %d busy resident(s) to go idle before unloading: %s",
                 len(busy), [r["model_id"].rsplit("/", 1)[-1] for r in busy])
        _sleep(5.0)
        try:
            res = residents(base_url, api_key, headers)
        except StatusUnavailable:
            continue
        targets = [r for r in res if r["model_id"] and r["model_id"] not in keep]
    unloaded = 0
    for r in targets:
        mid = r["model_id"]
        try:
            code, data = _mgmt("POST", base_url, api_key, headers,
                               f"/api/models/{_quote(mid)}/unload", timeout=WARMUP_TIMEOUT_S)
        except StatusUnavailable as e:
            log.warning("unload of %s failed: %s", mid, e)
            continue
        if code < 400:
            unloaded += 1
        else:
            log.warning("unload of %s rejected (HTTP %d: %s)", mid, code, str(data)[:200])
    log.info("unloaded %d/%d idle StudioForge model(s)", unloaded, len(targets))
    return unloaded


def unload_models(base_url: str, api_key: str, headers: dict | None,
                  model_ids: list[str], timeout_s: float = 20.0) -> int:
    """Best-effort, bounded unload of exactly ``model_ids`` (the stop path:
    never waits for idle, never raises, never sleeps). Returns how many the
    rig accepted."""
    n = 0
    for mid in model_ids:
        try:
            code, data = _mgmt("POST", base_url, api_key, headers,
                               f"/api/models/{_quote(mid)}/unload", timeout=timeout_s)
        except StudioForgeError as e:
            log.warning("unload of %s on stop failed: %s", mid, e)
            continue
        if code < 400:
            n += 1
        else:
            log.warning("unload of %s on stop rejected (HTTP %d: %s)", mid, code,
                        str(data)[:160])
    return n


# ------------------------------------------------------------------- leases

def list_leases(base_url: str, api_key: str, headers: dict | None = None) -> list[dict]:
    code, data = _mgmt("GET", base_url, api_key, headers, "/api/leases", timeout=15)
    if code != 200 or not isinstance(data, dict):
        raise StatusUnavailable(f"GET /api/leases HTTP {code}", status=code)
    return list(data.get("leases") or [])


# ------------------------------------------------- bench-first (2026-09-08)
# maintainer 2026-09-08: "The benchmark should take priority over everything, and
# basically shut down all other processes when running. It needs to evict all
# running models, including the image generation. If the benchmark can be
# accomplished on only the 5090s, do that and leave the 3090s free, otherwise
# reserve the entire rig for the benchmarks until complete."
#
# Three pieces implement that, all opt-in per provider in models.yaml:
#   force_evict: true            — every lease claim starts with force=true
#                                  (the standing authorisation FIX-2 wanted).
#   lease_devices_preferred: [0,1] — ask the rig's own planner whether the
#                                  model fits those cards empty; lease only
#                                  them if so, else ``lease_devices`` (all).
#   render_vacate_mcp: <url>     — a foreign RENDER lease (the image server)
#                                  on the wanted cards is vacated through
#                                  the image server's own ``comfy_control("vacate")``
#                                  (drain, free VRAM, release its lease) and
#                                  resumed when ours is released.
# force=true never breaks a foreign LEASE (the rig treats leases as
# first-class), so without the vacate step the judge phase would still be
# refused by the render lease — that was the 2026-08-22 outage class.

def placement_fits(base_url: str, api_key: str, headers: dict | None,
                   model_id: str, devices: list[int]) -> bool | None:
    """Does ``model_id`` fit on exactly ``devices`` with those cards EMPTY,
    per ``GET /api/models/{id}/options`` → ``placements[]``? ``None`` = the
    server has no placement for that device set (or did not answer), which
    callers treat as "no"."""
    from urllib.parse import quote
    try:
        code, data = _mgmt("GET", base_url, api_key, headers,
                           f"/api/models/{quote(model_id, safe='')}/options", timeout=60)
    except StatusUnavailable as e:
        log.warning("placement check for %s unavailable: %s", model_id, e)
        return None
    if code != 200 or not isinstance(data, dict):
        return None
    model = data.get("model") if isinstance(data.get("model"), dict) else data
    for pl in model.get("placements") or []:
        if sorted(int(d) for d in (pl.get("devices") or [])) == sorted(devices):
            opt = pl.get("optimal")
            return bool(isinstance(opt, dict) and opt.get("fits"))
    return None


def foreign_held_devices(leases: list[dict], *, vacatable_render: bool = False,
                         our_family: str = LEASE_HOLDER) -> set[int]:
    """Cards held by SOMEONE ELSE's lease (not a ``crucibleforge*`` holder).
    A render lease (the image server's ComfyUI) is left out when ``vacatable_render``
    — bench-first vacates it through the image server and resumes it afterwards.
    Everything else (SpeechForge's voice lease on GPU 3, another tenant) is
    never asked for: STEP4 defect 2 (2026-10-28) was a judge lease naming all
    four cards, GPU 3 included, while speechforge held it."""
    out: set[int] = set()
    for l in leases or []:
        holder = str(l.get("holder") or "")
        fam = str(l.get("holder_family") or "")
        if holder.startswith(our_family) or fam == our_family:
            continue
        render = l.get("kind") == "render"
        if render and vacatable_render:
            continue
        out.update(int(d) for d in (l.get("devices") or []))
    return out


def choose_lease_devices(base_url: str, api_key: str, headers: dict | None, model_id: str,
                         preferred: list[int] | None, fallback: list[int],
                         busy: set[int] | None = None) -> list[int]:
    """``preferred`` when the planner says the model fits there empty;
    ``fallback`` minus the cards another holder leases (``busy``) when the
    planner says it does NOT fit; and ``preferred`` again when the planner
    cannot say (no placement / no answer) — an unknown placement is never a
    reason to widen the lease to every card (EVAL-FABLE M9: the judge's
    fallback named [0,1,2,3], GPU 3 included, on 2026-10-28). Logged either
    way so the run's device choice is never a mystery."""
    busy = set(busy or ())
    free = [d for d in fallback if d not in busy]
    if busy & set(fallback):
        log.info("cards %s are leased by another holder — never asked for", sorted(busy & set(fallback)))
    if not preferred or sorted(preferred) == sorted(fallback):
        if not free:
            raise StudioForgeError(f"no free card for {model_id.rsplit('/', 1)[-1]}: every one of "
                                   f"{fallback} is leased by another holder")
        return free
    fits = placement_fits(base_url, api_key, headers, model_id, list(preferred))
    short = model_id.rsplit("/", 1)[-1]
    if fits:
        log.info("%s fits on preferred cards %s — leasing only those (the rest stay free)",
                 short, preferred)
        return list(preferred)
    if fits is None:
        log.warning("%s: the planner gave no placement for preferred cards %s — leasing those "
                    "(never widened to every card on an unknown placement); the rig refuses "
                    "clearly if it does not fit", short, preferred)
        return list(preferred)
    if not free:
        raise StudioForgeError(f"{short} does not fit on {preferred} and no other card is free "
                               f"(leased by another holder: {sorted(busy)})")
    log.info("%s does not fit on preferred cards %s — leasing the free set %s", short,
             preferred, free)
    return free


# ------------------------------------------- day lease mode (2026-11-06)
# MakoBench operator, OPERATOR-PLAN-v2 §6.3: a daytime bench is a guest. It
# takes ONE card that no chat/agent-tier (priority 1/2) or pinned model is
# recorded on, never vacates a render lease, never forces, and refuses when
# the model would fit only on a protected card.

class DayCardRefused(StudioForgeError):
    """No unprotected single card can hold the model (day lease mode)."""


def protected_devices(base_url: str, api_key: str, headers: dict | None = None) -> set[int]:
    """Cards a day run must not take: the recorded placement
    (``settings.device_override`` / ``settings.allowed_devices``) of every
    library model whose effective tier is 1/2 or that is pinned, plus the
    live devices of every loaded instance of such a model. A protected model
    with no recorded placement (may land anywhere) protects only the cards it
    is loaded on right now. Raises StatusUnavailable when the library cannot
    be read (a day run then refuses — it cannot tell what is protected)."""
    code, data = _mgmt("GET", base_url, api_key, headers, "/api/models", timeout=30)
    if code != 200 or not isinstance(data, (dict, list)):
        raise StatusUnavailable(f"GET /api/models HTTP {code}", status=code)
    rows = data.get("models", data.get("data", [])) if isinstance(data, dict) else data
    out: set[int] = set()
    protected_ids: set[str] = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        st = r.get("settings") or {}
        try:
            tier = int(r.get("priority") if r.get("priority") is not None
                       else st.get("priority") or 3)
        except (TypeError, ValueError):
            tier = 3
        if tier in (1, 2) or st.get("pinned") or r.get("pinned"):
            protected_ids.add(str(r.get("id") or r.get("model_id") or ""))
            for key in ("device_override", "allowed_devices"):
                out.update(int(d) for d in (st.get(key) or []))
    try:
        for m in status(base_url, api_key, headers).get("loaded", []):
            plan = m.get("plan") or {}
            try:
                tier = int(m.get("priority") or 3)
            except (TypeError, ValueError):
                tier = 3
            if m.get("model_id") in protected_ids or tier in (1, 2) or m.get("pinned"):
                out.update(int(d) for d in (plan.get("devices") or []))
    except StudioForgeError as e:
        raise StatusUnavailable(f"day mode: live placements unreadable ({e})",
                                status=getattr(e, "status", None)) from e
    return out


def choose_day_card(base_url: str, api_key: str, headers: dict | None, model_id: str,
                    exclude=()) -> list[int]:
    """The ONE card a day-mode lease names: the first rig card that is not
    protected (:func:`protected_devices`), not leased by anyone else (render
    leases included — a day run never vacates) and not ``exclude``d, on
    which the planner says ``model_id`` fits EMPTY. Refuses
    (:class:`DayCardRefused`, status 409) otherwise, naming whether the model
    would have fitted on a protected card."""
    short = model_id.rsplit("/", 1)[-1]
    exclude = {int(d) for d in exclude or ()}
    cards = gpu_indices(base_url, api_key, headers)
    protected = protected_devices(base_url, api_key, headers)
    leased = foreign_held_devices(list_leases(base_url, api_key, headers),
                                  vacatable_render=False)
    free = [d for d in cards if d not in protected and d not in leased and d not in exclude]
    for d in free:
        if placement_fits(base_url, api_key, headers, model_id, [d]):
            log.info("day mode: %s fits on unprotected card %d — leasing only that card",
                     short, d)
            return [d]
    on_protected = [d for d in cards if d in protected and d not in exclude
                    and placement_fits(base_url, api_key, headers, model_id, [d])]
    if on_protected:
        raise DayCardRefused(
            f"day mode: {short} fits only on protected card(s) {on_protected} "
            f"(priority-1/2 or pinned placements) — refused; it runs in the night window",
            status=409)
    raise DayCardRefused(
        f"day mode: {short} fits on no single free card (free: {free or 'none'}, "
        f"protected: {sorted(protected)}, leased by others: {sorted(leased)}) — refused; "
        f"it runs in the night window", status=409)


# ---------------------------------------------- stale leases (STEP4 2026-10-28)

def stale_leases(leases: list[dict], holder: str, model_id: str,
                 own_lease_id: str | None = None) -> list[dict]:
    """Leases held under OUR exact ``holder`` that name ``model_id`` and are
    not this process's own: left by a run that died without releasing
    (STEP4's 92fa55704e1e, a SIGABRTed run). One benchmark runs at a time
    (results/.rig.lock, and a resident another live run leased is refused
    outright — Provider._refuse_other_bench_lease), so such a lease has no
    live owner. Other holders (the judge's ``crucibleforge-judge`` vs a run's
    ``crucibleforge``), other models and other tenants are never touched."""
    out = []
    for l in leases or []:
        lid = str(l.get("id") or l.get("lease_id") or "")
        if str(l.get("holder") or "") != holder or (own_lease_id and lid == own_lease_id):
            continue
        if model_id in (l.get("model_ids") or []):
            out.append(l)
    return out


def release_stale_leases(base_url: str, api_key: str, headers: dict | None, holder: str,
                         model_id: str, own_lease_id: str | None = None) -> list[str]:
    """Release the :func:`stale_leases` before taking a new one, so a crashed
    run's lease is never stacked under a second one. Returns the ids
    released. An unreadable lease list changes nothing."""
    try:
        leases = list_leases(base_url, api_key, headers)
    except StudioForgeError as e:
        log.info("lease list unavailable (%s) — no stale-lease check", e)
        return []
    out = []
    for l in stale_leases(leases, holder, model_id, own_lease_id):
        lid = str(l.get("id") or l.get("lease_id") or "")
        log.warning("stale lease %s (holder %s, devices %s, reason %r) from a run that is gone "
                    "— releasing it before taking a new one", lid, holder, l.get("devices"),
                    str(l.get("reason") or "")[:120])
        if lid and release_lease(base_url, api_key, headers, lid):
            out.append(lid)
    return out


def foreign_render_leases(leases: list[dict], devices: list[int],
                          our_families=("crucibleforge",)) -> list[dict]:
    """Standing leases held by someone else's RENDER tenant (an image
    server's ComfyUI) that touch ``devices``. Family/kind match, never the exact
    holder string (the judge-phase outage was an exact-string match)."""
    want = set(int(d) for d in devices)
    out = []
    for l in leases or []:
        fam = str(l.get("holder_family") or "")
        holder = str(l.get("holder") or "")
        if fam in our_families or holder.startswith("crucibleforge"):
            continue
        render = l.get("kind") == "render"
        if not render:
            continue
        if want & set(int(d) for d in (l.get("devices") or [])):
            out.append(l)
    return out


def render_server_control(mcp_url: str, action: str, timeout: float = 180.0) -> dict:
    """``comfy_control(action)`` over the image server's streamable-http MCP
    endpoint (minimal streamable-http client). Raises
    StudioForgeError on any refusal so callers never mistake a failed vacate
    for a free card."""
    hdr = {"content-type": "application/json", "accept": "application/json, text/event-stream"}

    def _body(resp):
        txt = resp.text
        for line in txt.splitlines():
            if line.startswith("data:"):
                return json_loads(line[5:])
        return json_loads(txt) if txt.strip().startswith("{") else {}

    try:
        with httpx.Client(timeout=_timeout(timeout)) as http:
            init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "crucibleforge", "version": "1"}}}
            r = http.post(mcp_url, json=init, headers=hdr)
            sid = r.headers.get("mcp-session-id")
            body = _body(r)
            if not sid or "error" in body:
                raise StudioForgeError(f"image server MCP init failed: {body.get('error') or 'no session id'}")
            hdr["mcp-session-id"] = sid
            http.post(mcp_url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=hdr)
            call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "comfy_control", "arguments": {"action": action}}}
            r = http.post(mcp_url, json=call, headers=hdr)
            body = _body(r)
    except httpx.HTTPError as e:
        raise StudioForgeError(f"image server MCP {action}: {e}") from e
    if "error" in body:
        raise StudioForgeError(f"image server comfy_control({action}) refused: {str(body['error'])[:300]}")
    res = body.get("result") or {}
    if res.get("isError"):
        raise StudioForgeError(f"image server comfy_control({action}) failed: {str(res)[:300]}")
    return res


def vacate_render_leases(base_url: str, api_key: str, headers: dict | None, devices: list[int],
                         render_vacate_mcp: str | None, wait_s: float = 240.0) -> bool:
    """If a foreign render lease stands on ``devices``, ask the image server to
    vacate (drain + free VRAM + release its lease) and wait for the lease to
    disappear. Returns True when WE vacated it (so the caller resumes it
    later). Raises StudioForgeError if the lease is still there after
    ``wait_s`` — a lease that cannot be vacated is a real refusal, not a
    reason to run anyway."""
    try:
        leases = list_leases(base_url, api_key, headers)
    except StatusUnavailable as e:
        log.warning("cannot list leases before claiming %s: %s", devices, e)
        return False
    foreign = foreign_render_leases(leases, devices)
    if not foreign:
        return False
    names = [(l.get("holder"), l.get("devices")) for l in foreign]
    if not render_vacate_mcp:
        raise StudioForgeError(
            f"render lease(s) {names} hold cards {devices} and no render_vacate_mcp is configured "
            f"to vacate them (providers.<name>.render_vacate_mcp in models.yaml)")
    log.warning("bench-first: render lease(s) %s hold %s — asking the image server to vacate", names, devices)
    render_server_control(render_vacate_mcp, "vacate")
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait_s:
        _sleep(5)
        try:
            still = foreign_render_leases(list_leases(base_url, api_key, headers), devices)
        except StatusUnavailable:
            continue
        if not still:
            log.info("render lease released after %.0fs", time.monotonic() - t0)
            return True
    raise StudioForgeError(f"render lease(s) {names} still stand on {devices} after {wait_s:.0f}s vacate wait")


def _lease_id(data) -> str | None:
    if not isinstance(data, dict):
        return None
    for k in ("lease_id", "id"):
        if data.get(k):
            return str(data[k])
    lease = data.get("lease")
    if isinstance(lease, dict):
        for k in ("lease_id", "id"):
            if lease.get(k):
                return str(lease[k])
    return None


def _busy_models(res: dict) -> list[str]:
    """Model ids the rig names in a ``model_busy`` refusal (``error.studioforge
    .busy_models[].model_id``), else the one in the message text."""
    out = []
    err = res.get("error") if isinstance(res.get("error"), dict) else {}
    sf = err.get("studioforge") or res.get("studioforge") or {}
    for b in sf.get("busy_models") or []:
        if isinstance(b, dict) and b.get("model_id"):
            out.append(str(b["model_id"]))
    if not out:
        msg = str(err.get("message") or res.get("message") or "")
        m = re.match(r"\s*(\S+) \(\d+ in flight\) is serving", msg)
        if m:
            out.append(m.group(1))
    return out


def unload_model(base_url: str, api_key: str, headers: dict | None, model_id: str) -> bool:
    """``POST /api/models/{id}/unload`` — stops the child serving the model,
    mid-request included. Open on the rig except against a GPU lease."""
    from urllib.parse import quote
    code, data = _mgmt("POST", base_url, api_key, headers,
                       f"/api/models/{quote(model_id, safe='')}/unload", timeout=120)
    if code >= 400:
        log.warning("unload of %s refused (HTTP %d): %s", model_id.rsplit("/", 1)[-1], code,
                    str(data)[:200])
        return False
    return True


def acquire_lease(base_url: str, api_key: str, headers: dict | None, devices: list[int],
                  model_ids: list[str] | None = None, holder: str = LEASE_HOLDER,
                  reason: str = "", idle_ttl_s: float | None = LEASE_IDLE_TTL_S,
                  force: bool = False, wait_busy_s: float = DEFAULT_WAIT_BUSY_S,
                  busy_unload_after_s: float | None = None) -> dict:
    """POST /api/leases — give ``devices`` to ``holder`` and load ``model_ids``
    onto exactly them.

    Two refusal dialects are recognised and both are WAITED OUT, never
    escalated:

    - A resident mid-request, or the D46 "you do not outrank this" tier
      refusal: retried on ``retry_after_s`` when the server gives one, else
      (D46 gives none) on a fixed ``_TIER_REFUSAL_POLL_S`` cadence — both
      bounded by the same ``wait_busy_s`` budget. See ``_tier_refusal_reason`` /
      RC-1.
    - A PINNED idle resident ("pinned model(s) … pass force=true"): this
      function never sets ``force=true`` on its own initiative for this or
      any other refusal — an earlier version did exactly that (RC-1) and it
      is precisely how a chat-tier pinned model got silently evicted. If
      the *caller* passed ``force=True`` from the start, that stands for the
      whole retry loop and a pinned refusal is granted immediately; if not,
      a pinned (or any other unrecognised) 409 is raised as a **final**
      error naming the resident — evicting it is a decision one layer up
      (the ``--force-evict`` CLI flag, itself gated on the maintainer's ``--go``), not
      something this function decides for you.

    ``busy_unload_after_s`` (bench-first, maintainer 2026-09-08: "evict all
    running models") — when set, a ``model_busy`` refusal that persists past
    that many seconds is answered by UNLOADING the busy resident
    (``POST /api/models/{id}/unload``, which the rig allows and which does cut
    its in-flight request) and re-claiming. ``None`` = the pre-2026-09-08
    behaviour: wait the whole ``wait_busy_s`` and then fail. Only ever set
    from the provider's standing policy; a foreign client that streams
    back-to-back never leaves a 15 s poll a gap to win (run 2, 09:07-09:17,
    56 requests in 15 min, always one in flight).

    Returns the server's lease record with ``_lease_id`` filled in."""
    body = {"devices": [int(d) for d in devices], "model_ids": model_ids,
            "holder": holder, "reason": reason, "idle_ttl_s": idle_ttl_s, "force": force}
    busy_since: float | None = None
    unloaded: set[str] = set()
    if force:
        log.warning("GPU lease request for %s starts with force=true (caller-authorised "
                    "eviction) — a pinned or tiered idle resident WILL be evicted if one is "
                    "in the way: holder=%s devices=%s reason=%r",
                    model_ids, holder, body["devices"], reason)
    waited = 0.0
    while True:
        code, data = _mgmt("POST", base_url, api_key, headers, "/api/leases",
                           json=body, timeout=WARMUP_TIMEOUT_S)
        if code < 400:
            lid = _lease_id(data)
            if not lid:
                raise StudioForgeError(f"lease created but no lease id in reply: {str(data)[:200]}")
            out = dict(data) if isinstance(data, dict) else {"raw": data}
            out["_lease_id"] = lid
            log.info("GPU lease %s acquired: devices=%s models=%s holder=%s", lid,
                     body["devices"], model_ids, holder)
            return out
        res = data if isinstance(data, dict) else {"_status": code}
        detail = str(res.get("detail") or res.get("error") or res)[:300]
        wait = _retry_wait(res, waited, wait_busy_s) if code in (503, 507, 409) else None
        tier_reason = (_tier_refusal_reason(code, res, detail)
                      if wait is None and waited < wait_busy_s else None)
        if tier_reason:
            wait = min(_TIER_REFUSAL_POLL_S, wait_busy_s - waited)
        if wait is not None:
            reason_txt = tier_reason or _refusal_reason(res)
            busy = _busy_models(res) if code == 503 else []
            if busy and busy_unload_after_s is not None:
                busy_since = waited if busy_since is None else busy_since
                if waited - busy_since >= busy_unload_after_s:
                    for mid in busy:
                        if mid in unloaded:
                            continue
                        log.warning("bench-first: %s has been mid-request for %.0fs — unloading "
                                    "it (its in-flight request is cut; it is reloaded when the "
                                    "run ends if it was resident before)",
                                    mid.rsplit("/", 1)[-1], waited - busy_since)
                        unload_model(base_url, api_key, headers, mid)
                        unloaded.add(mid)
                    wait = min(wait, 5.0)
            elif not busy:
                busy_since = None
            log.warning("lease refused (HTTP %d, %s) — retrying in %.0fs: %s",
                        code, reason_txt, wait, detail)
            _sleep(wait)
            waited += wait
            continue
        raise StudioForgeError(f"lease refused (HTTP {code}): {detail}", status=code,
                               retry_after_s=res.get("_retry_after_s"),
                               suggestions=_suggestions(res))


def release_lease(base_url: str, api_key: str, headers: dict | None, lease_id: str) -> bool:
    try:
        code, data = _mgmt("DELETE", base_url, api_key, headers, f"/api/leases/{lease_id}",
                           timeout=60)
    except StatusUnavailable as e:
        log.warning("lease %s release failed: %s", lease_id, e)
        return False
    if code >= 400 and code != 404:
        log.warning("lease %s release rejected (HTTP %d: %s)", lease_id, code, str(data)[:200])
        return False
    log.info("GPU lease %s released", lease_id)
    return True


def touch_lease(base_url: str, api_key: str, headers: dict | None, lease_id: str) -> bool:
    try:
        code, _ = _mgmt("POST", base_url, api_key, headers, f"/api/leases/{lease_id}/touch",
                        timeout=30)
    except StatusUnavailable:
        return False
    return code < 400
