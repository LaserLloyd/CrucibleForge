"""StudioForge model backend — a llama.cpp-based multi-model OpenAI-compatible HTTP server.

Facts encoded here (verified 2026-08-17):
- Endpoint = ``providers.<name>.base_url`` (typically no auth on a private network).
- GET /v1/models lists every downloaded model by its full id
  (publisher/repo/file-stem). Each entry carries ``studioforge.state``
  ("loaded" / "not-loaded"), ``studioforge.size_bytes`` and ``studioforge.kind``.
- POST /v1/chat/completions auto-loads the requested model on first request and
  self-manages VRAM (evicts idle models). No explicit load is needed to serve a
  model; explicit load/unload exists only via the StudioForge MCP (:1234/mcp),
  which the bench does not need for inference.
- The served model id is echoed back in every completion (same as LM Studio),
  so the api.WrongModelError guard still applies unchanged.

The bench's notion of "load" for StudioForge is therefore a tiny warm-up
completion that triggers the JIT load and proves the model can serve.
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


class StudioForgeError(RuntimeError):
    """StudioForge could not serve the requested model."""


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


def _api(base_url: str) -> str:
    """StudioForge's management API lives beside /v1 (…/api/*)."""
    return base_url[:-3] if base_url.endswith("/v1") else base_url


def _quote(model_id: str) -> str:
    from urllib.parse import quote
    return quote(model_id, safe="")


def loaded_plan(model_id: str, base_url: str, api_key: str) -> dict | None:
    """The server's live plan for a loaded model (parallel slots, ctx, devices)
    from GET /api/status, or None if it is not loaded."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=15) as http:
            resp = http.get(f"{_api(base_url)}/api/status", headers=headers)
        if resp.status_code != 200:
            return None
        for m in resp.json().get("loaded", []):
            if m.get("model_id") == model_id:
                return {"state": m.get("state"), **(m.get("plan") or {})}
    except (httpx.HTTPError, ValueError):
        return None
    return None


def recommended_load(model_id: str, base_url: str, api_key: str,
                     context_length: int | None = None) -> dict | None:
    """Pick the server's best placement profile (GET /api/models/<id>/profiles)
    and return its load_args — the 'recommended loading'. Preference: profiles
    that fit, then the highest estimated single-stream gen tok/s, then the most
    parallel slots. Returns None when the endpoint is unavailable (older
    server) so the caller falls back to a plain JIT load."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=30) as http:
            resp = http.get(f"{_api(base_url)}/api/models/{_quote(model_id)}/profiles",
                            headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    profs = [p for p in data.get("profiles", []) if p.get("fits")]
    if not profs:
        return None
    profs.sort(key=lambda p: (-(p.get("est_gen_tps") or 0), -(p.get("max_parallel") or 0)))
    best = profs[0]
    args = dict(best.get("load_args") or {})
    args.pop("model_id", None)
    if not args.get("parallel"):
        args["parallel"] = best.get("max_parallel") or 1
    if context_length and args.get("ctx_size") and context_length < args["ctx_size"]:
        args["ctx_size"] = int(context_length)  # never ask for more than the registry wants
    args["_profile"] = {"mode": best.get("mode"), "est_gen_tps": best.get("est_gen_tps"),
                        "est_gen_tps_batched": best.get("est_gen_tps_batched"),
                        "vram_gib": best.get("vram_gib")}
    return args


def warm_model(model_id: str, base_url: str, api_key: str) -> float:
    """Trigger a JIT load with a tiny completion; return seconds to first
    response. Raises StudioForgeError if the model can't serve."""
    from .api import TransportError, WrongModelError, stream_chat_retried
    t0 = time.perf_counter()
    try:
        stream_chat_retried(base_url, api_key, model_id,
                            [{"role": "user", "content": "Hi"}],
                            max_tokens=4, temperature=0.0, seed=42)
    except (TransportError, WrongModelError) as e:
        raise StudioForgeError(f"StudioForge warm-up of {model_id} failed: {e}") from e
    return time.perf_counter() - t0


def load_model(model_id: str, base_url: str, api_key: str,
               context_length: int | None = None,
               recommended: bool = True) -> float:
    """Load a StudioForge model the way the server recommends (placement
    profile with parallel slots), then warm it. Falls back to a plain JIT
    warm-up when the management API is unavailable. Returns load seconds.
    A model already loaded with the recommended slot count is left alone."""
    t0 = time.perf_counter()
    args = recommended_load(model_id, base_url, api_key, context_length) if recommended else None
    if args:
        prof = args.pop("_profile", {})
        live = loaded_plan(model_id, base_url, api_key)
        want_par = int(args.get("parallel") or 1)
        if live and int(live.get("parallel") or 1) >= want_par and live.get("state") == "ready":
            log.info("%s already loaded with parallel=%s", model_id, live.get("parallel"))
            return 0.0
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        body = {k: v for k, v in args.items() if k in ("ctx_size", "kv_cache_type", "parallel")}
        body["force"] = True
        log.info("loading %s via recommended profile %s (parallel=%s, ctx=%s, est %s tok/s "
                 "single / %s batched)", model_id, prof.get("mode"), body.get("parallel"),
                 body.get("ctx_size"), prof.get("est_gen_tps"), prof.get("est_gen_tps_batched"))
        try:
            with httpx.Client(timeout=WARMUP_TIMEOUT_S) as http:
                if live:
                    http.post(f"{_api(base_url)}/api/models/{_quote(model_id)}/unload",
                              headers=headers)
                resp = http.post(f"{_api(base_url)}/api/models/{_quote(model_id)}/load",
                                 json=body, headers=headers)
            if resp.status_code >= 400:
                log.warning("recommended load rejected (HTTP %d: %s) — falling back to JIT",
                            resp.status_code, resp.text[:200])
        except httpx.HTTPError as e:
            log.warning("recommended load failed (%s) — falling back to JIT", e)
    warm_model(model_id, base_url, api_key)
    live = loaded_plan(model_id, base_url, api_key) or {}
    log.info("%s serving: parallel=%s ctx=%s devices=%s", model_id,
             live.get("parallel"), live.get("ctx_size"), live.get("devices"))
    return time.perf_counter() - t0


def loaded_parallel(model_id: str, base_url: str, api_key: str) -> int:
    """Parallel slots the server currently runs this model with (1 if unknown)."""
    live = loaded_plan(model_id, base_url, api_key) or {}
    try:
        return max(1, int(live.get("parallel") or 1))
    except (TypeError, ValueError):
        return 1


def unload_all(base_url: str = DEFAULT_BASE_URL,
               api_key: str = DEFAULT_API_KEY) -> int:
    """Unload every model currently resident in VRAM, so the next load (e.g.
    the judge) gets a clean slate instead of an out-of-VRAM HTTP 507. Returns
    the number of models unloaded (0 when nothing was loaded or the server is
    unreachable)."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    loaded = [m["identifier"] for m in loaded_models(base_url, api_key)
              if m.get("identifier")]
    if not loaded:
        return 0
    unloaded = 0
    for model_id in loaded:
        try:
            with httpx.Client(timeout=WARMUP_TIMEOUT_S) as http:
                resp = http.post(f"{_api(base_url)}/api/models/{_quote(model_id)}/unload",
                                 headers=headers)
            if resp.status_code < 400:
                unloaded += 1
            else:
                log.warning("unload of %s rejected (HTTP %d: %s)",
                            model_id, resp.status_code, resp.text[:200])
        except httpx.HTTPError as e:
            log.warning("unload of %s failed: %s", model_id, e)
    log.info("unloaded %d/%d StudioForge model(s)", unloaded, len(loaded))
    return unloaded
