"""Provider abstraction: where a model is served and how it is managed.

Three provider types cover every backend Gauntlet talks to:

- ``openai`` — any OpenAI-compatible ``/chat/completions`` server that needs no
  load management: DeepSeek, OpenRouter, OpenAI, Groq, Together, Mistral,
  Open WebUI (``/api``), Ollama (``/v1``), vLLM, llama.cpp ``llama-server``,
  and LM Studio when you don't want the ``lms`` CLI managing loads. Models are
  simply requested by id. API keys come from ``api_key_env``.
- ``lmstudio`` — LM Studio managed through the ``lms`` CLI: one model loaded at
  a time, explicit load with verification (load time is a metric), snapshot/
  restore of whatever was loaded before the run. Includes the wrong-model
  guard: LM Studio answers 200 from whatever is loaded even for a bogus id.
- ``studioforge`` — a llama.cpp-based multi-model server (StudioForge) that
  JIT-loads on first request; "load" = a warm-up completion timed as load_s.

Every provider exposes the same small surface, so the runner/judge/pairwise
code never branches on backend. Per-provider ``concurrency`` lets remote APIs
run cases in parallel (local servers default to 1).
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field

import httpx

from . import lms, studioforge
from .config import ConfigError, provider_of

log = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    pass


@dataclass
class Provider:
    name: str
    type: str
    base_url: str
    api_key: str = ""
    concurrency: int = 1
    verify_model: bool = True
    headers: dict = field(default_factory=dict)
    timeout: float = 900.0
    extra_body: dict = field(default_factory=dict)
    recommended_load: bool = True
    concurrency_explicit: bool = False
    # remembers what /models returned (None = endpoint doesn't support listing)
    _models_cache: set | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ---------------------------------------------------------- discovery
    def alive(self) -> bool:
        headers = self._headers()
        try:
            with httpx.Client(timeout=10) as http:
                resp = http.get(f"{self.base_url}/models", headers=headers)
                if resp.status_code == 200:
                    return True
                # Some gateways 401/404 on /models but serve completions fine.
                # Treat "reachable but no listing" as alive for openai type.
                return self.type == "openai" and resp.status_code in (401, 403, 404, 405)
        except Exception:
            return False

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", **self.headers}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def list_models(self, refresh: bool = False) -> set[str]:
        """Model ids the provider says it serves. Empty set means "unknown /
        listing unsupported" — callers must not treat that as 'no models'."""
        with self._lock:
            if self._models_cache is not None and not refresh:
                return set(self._models_cache)
        ids: set[str] = set()
        if self.type == "lmstudio":
            try:
                ids = {m["modelKey"] for m in lms.list_models()}
            except lms.LmsError as e:
                log.warning("lms ls failed: %s", e)
        elif self.type == "studioforge":
            ids = set(studioforge.list_models(self.base_url, self.api_key))
        else:
            try:
                with httpx.Client(timeout=15) as http:
                    resp = http.get(f"{self.base_url}/models", headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("data", data) if isinstance(data, dict) else data
                    for m in items or []:
                        mid = m.get("id") if isinstance(m, dict) else m
                        if mid:
                            ids.add(str(mid))
            except Exception as e:
                log.debug("%s: /models listing failed: %s", self.name, e)
        with self._lock:
            self._models_cache = set(ids)
        return ids

    def is_available(self, model_id: str) -> bool:
        ids = self.list_models()
        if not ids:
            # listing unsupported: optimistic — the wrong-model / transport
            # errors on the first call are the real check
            return self.type == "openai"
        return model_id in ids

    # ------------------------------------------------------ load control
    def switch_model(self, model_id: str, context_length: int | None = None) -> float:
        """Make ``model_id`` the served model. Returns load seconds (0 for
        providers with no load step). StudioForge loads the server's
        recommended placement profile (parallel slots) unless the provider
        sets ``recommended_load: false``."""
        if self.type == "lmstudio":
            return lms.switch_model(model_id, context_length)
        if self.type == "studioforge":
            studioforge.unload_all(self.base_url, self.api_key)
            return studioforge.load_model(model_id, self.base_url, self.api_key,
                                          context_length,
                                          recommended=self.recommended_load)
        return 0.0

    def workers(self, model_id: str) -> int:
        """How many requests to run at once for this model. openai: the
        configured concurrency. studioforge: the loaded model's parallel slot
        count, capped by the configured concurrency when one is set
        (``concurrency: auto`` = follow the server). lmstudio: 1."""
        if self.type == "openai":
            return self.concurrency
        if self.type == "studioforge":
            slots = studioforge.loaded_parallel(model_id, self.base_url, self.api_key)
            return min(slots, self.concurrency) if self.concurrency_explicit else slots
        return 1

    def is_loaded(self, model_id: str) -> bool:
        if self.type == "lmstudio":
            return lms.is_loaded(model_id)
        return True  # JIT / stateless providers are always "loaded"

    def unload_all(self) -> None:
        if self.type == "lmstudio":
            lms.unload_all()
        elif self.type == "studioforge":
            studioforge.unload_all(self.base_url, self.api_key)

    def loaded_models(self) -> list[dict]:
        if self.type == "lmstudio":
            return list(lms.loaded_models())
        if self.type == "studioforge":
            return studioforge.loaded_models(self.base_url, self.api_key)
        return []

    def snapshot(self):
        """State to restore after a run (only LM Studio has any)."""
        if self.type == "lmstudio":
            try:
                return lms.snapshot()
            except lms.LmsError:
                return []
        return None

    def restore(self, state) -> None:
        if self.type == "lmstudio" and state:
            lms.restore(state)

    # ------------------------------------------------------------ chat
    def chat(self, model_id: str, messages: list[dict], **kw):
        """One streamed completion with retries (see api.stream_chat_retried)."""
        from .api import stream_chat_retried
        body_extra = {**self.extra_body, **(kw.pop("extra_body", None) or {})}
        kw.setdefault("timeout", self.timeout)
        return stream_chat_retried(self.base_url, self.api_key, model_id, messages,
                                   headers=self.headers, verify_model=self.verify_model,
                                   extra_body=body_extra, **kw)


# ------------------------------------------------------------- registry

_CACHE: dict[tuple[int, str], Provider] = {}


def _resolve_key(p: dict) -> str:
    env_name = p.get("api_key_env")
    if env_name:
        val = os.environ.get(env_name, "")
        if not val:
            log.warning("provider %s: $%s is not set — requests will be unauthenticated",
                        p.get("name"), env_name)
        return val
    key = p.get("api_key") or ""
    # allow "${ENV}" placeholders too (OpenClaw-style)
    if isinstance(key, str) and key.startswith("${") and key.endswith("}"):
        return os.environ.get(key[2:-1], "")
    return str(key)


def get_provider(cfg: dict, name: str) -> Provider:
    key = (id(cfg), name)
    if key in _CACHE:
        return _CACHE[key]
    p = provider_of(cfg, name)
    ptype = p.get("type", "openai")
    conc_raw = p.get("concurrency", 1 if ptype != "studioforge" else "auto")
    conc_auto = str(conc_raw).lower() == "auto"
    prov = Provider(
        name=name, type=ptype, base_url=str(p["base_url"]).rstrip("/"),
        api_key=_resolve_key(p),
        concurrency=1 if conc_auto else max(1, int(conc_raw)),
        concurrency_explicit=not conc_auto,
        recommended_load=bool(p.get("recommended_load", True)),
        verify_model=bool(p.get("verify_model", ptype != "openai")),
        headers=dict(p.get("headers") or {}),
        timeout=float(p.get("timeout", 900)),
        extra_body=dict(p.get("extra_body") or {}),
    )
    _CACHE[key] = prov
    return prov


def provider_for(cfg: dict, entry: dict) -> Provider:
    """Provider serving a registry model entry (or judge candidate)."""
    name = entry.get("provider")
    if not name:
        raise ConfigError(f"entry {entry.get('name') or entry.get('model_id')} has no provider")
    return get_provider(cfg, name)


def all_providers(cfg: dict) -> list[Provider]:
    return [get_provider(cfg, n) for n in cfg["providers"]]


def clear_cache() -> None:
    _CACHE.clear()


def model_extra_body(entry: dict) -> dict:
    """Per-model request extras (v3 ``extra_body``; v2 ``studioforge`` was
    upgraded into it by config.upgrade_legacy)."""
    return dict(entry.get("extra_body") or {})


def price_of(entry: dict) -> tuple[float, float] | None:
    """(input, output) USD per 1M tokens, or None if not priced (local)."""
    price = entry.get("price")
    if not price:
        return None
    try:
        return float(price.get("input", 0)), float(price.get("output", 0))
    except (TypeError, ValueError, AttributeError):
        return None


def cost_usd(entry: dict, prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    p = price_of(entry)
    if not p:
        return None
    return ((prompt_tokens or 0) * p[0] + (completion_tokens or 0) * p[1]) / 1_000_000
