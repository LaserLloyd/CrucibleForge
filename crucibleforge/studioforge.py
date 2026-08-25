"""StudioForge model backend — a llama.cpp-based multi-model OpenAI-compatible HTTP server.

Facts encoded here (verified against StudioForge 0.2.0, 2026-08-22):
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
  it). A resident mid-request makes the call a 503 + ``retry_after_s``;
  ``force`` only overrides a PINNED idle resident. Leases do not govern
  foreign VRAM holders (ComfyUI) — see GET /api/vram/holders.
- The served model id is echoed back in every completion, so the
  api.WrongModelError guard still applies unchanged.

Rig etiquette baked in (2026-08-22 postmortem): never evict a model that is
serving a request — wait for it (bounded) instead; never send ``force``;
never fall back to a JIT load at planner defaults when the server said the
requested window does not fit; wait for ``state == ready`` before the first
completion (a warm-up sent during ``loading`` makes the server plan a
second load that then 507s).
"""
from __future__ import annotations

import logging
import time

import httpx

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
# evicting it (a family bot's turn is seconds to a few minutes).
DEFAULT_WAIT_BUSY_S = 600.0
# Lease defaults: holder name the rig shows, and a safety TTL so a crashed
# client cannot hold the cards forever (the keepalive touches it meanwhile).
LEASE_HOLDER = "crucibleforge"
LEASE_IDLE_TTL_S = 7200.0


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
        with httpx.Client(timeout=timeout) as http:
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
        with httpx.Client(timeout=15) as http:
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
        time.sleep(poll_s)


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
                     headers: dict | None = None) -> dict | None:
    """StudioForge 0.2 ``POST /api/models/<id>/load-recommended``: the server
    picks placement, KV type and slot count for exactly ``ctx_size`` per slot
    (quality-first). Returns the response dict on success, ``{"_status": 507,
    "_retry_after_s": ..., "_suggestions": ...}`` when the window doesn't fit,
    or None when the endpoint doesn't exist (older server)."""
    body: dict = {"ctx_size": int(ctx_size)}
    if prefer_mode:
        body["prefer_mode"] = prefer_mode
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


def _retry_wait(res: dict, waited: float, wait_busy_s: float) -> float | None:
    """Seconds to sleep before re-trying a structured refusal, or None when
    the refusal is final (no hint, or the wait budget is spent)."""
    ra = res.get("_retry_after_s")
    if ra is None or waited >= wait_busy_s:
        return None
    return max(2.0, min(float(ra), 60.0, wait_busy_s - waited))


def load_model(model_id: str, base_url: str, api_key: str,
               context_length: int | None = None,
               recommended: bool = True, headers: dict | None = None,
               wait_busy_s: float = DEFAULT_WAIT_BUSY_S) -> float:
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
        while True:
            res = load_recommended(model_id, base_url, api_key, wanted, headers=headers)
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
                log.info("%s serving: parallel=%s ctx=%s devices=%s", model_id,
                         live.get("parallel"), live.get("ctx_size"), live.get("devices"))
                return time.perf_counter() - t0
            wait = _retry_wait(res, waited, wait_busy_s)
            detail = str(res.get("detail") or (res.get("error") or {}).get("message")
                         if isinstance(res.get("error"), dict) else res.get("error") or res)[:300]
            if wait is not None:
                log.warning("load-recommended for %s at ctx=%d refused (HTTP %s, a resident is "
                            "busy) — retrying in %.0fs (%s)", model_id, wanted,
                            res.get("_status"), wait, detail)
                time.sleep(wait)
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
        time.sleep(5.0)
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


# ------------------------------------------------------------------- leases

def list_leases(base_url: str, api_key: str, headers: dict | None = None) -> list[dict]:
    code, data = _mgmt("GET", base_url, api_key, headers, "/api/leases", timeout=15)
    if code != 200 or not isinstance(data, dict):
        raise StatusUnavailable(f"GET /api/leases HTTP {code}", status=code)
    return list(data.get("leases") or [])


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


def acquire_lease(base_url: str, api_key: str, headers: dict | None, devices: list[int],
                  model_ids: list[str] | None = None, holder: str = LEASE_HOLDER,
                  reason: str = "", idle_ttl_s: float | None = LEASE_IDLE_TTL_S,
                  force: bool = False, wait_busy_s: float = DEFAULT_WAIT_BUSY_S) -> dict:
    """POST /api/leases — give ``devices`` to ``holder`` and load ``model_ids``
    onto exactly them. A resident mid-request answers 503 + retry_after_s:
    that is waited out (bounded by ``wait_busy_s``), never forced. Returns the
    server's lease record with ``_lease_id`` filled in."""
    body = {"devices": [int(d) for d in devices], "model_ids": model_ids,
            "holder": holder, "reason": reason, "idle_ttl_s": idle_ttl_s, "force": force}
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
        if code == 409 and "pinned" in detail.lower() and not body["force"]:
            # a PINNED idle resident is in the way. force=true evicts it and
            # the server's pin reconciler brings it back when the lease ends
            # (the server still refuses a resident that is mid-request, force
            # or not) — exactly the "nothing else on the cards" the lease is for
            log.warning("lease blocked by a pinned idle resident — retrying with force=true "
                        "(the pin reconciler restores it after the lease): %s", detail)
            body["force"] = True
            continue
        wait = _retry_wait(res, waited, wait_busy_s) if code in (503, 507, 409) else None
        if wait is not None:
            log.warning("lease refused (HTTP %d, a resident is busy) — retrying in %.0fs: %s",
                        code, wait, detail)
            time.sleep(wait)
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
