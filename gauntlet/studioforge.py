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
               context_length: int | None = None) -> float:
    """'Load' a StudioForge model: a warm-up completion (JIT load). StudioForge
    manages its own context window; ``context_length`` is ignored."""
    return warm_model(model_id, base_url, api_key)


def unload_all(base_url: str = DEFAULT_BASE_URL,
               api_key: str = DEFAULT_API_KEY) -> None:
    """No-op: StudioForge self-manages VRAM and evicts idle models on its own."""
    return None
