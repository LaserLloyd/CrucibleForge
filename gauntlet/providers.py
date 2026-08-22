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

import atexit
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

import httpx

from . import lms, studioforge
from .config import ConfigError, provider_of

log = logging.getLogger(__name__)

# how often the keepalive touches a standing GPU lease (fraction of its TTL)
_LEASE_TOUCH_FRACTION = 0.25
# /api/status polls for eviction detection are cached this long
_STATUS_CACHE_S = 5.0


def merge_extra_body(base: dict | None, over: dict | None) -> dict:
    """Merge request extras one level deep for ``chat_template_kwargs`` so a
    recovery pass that flips ``enable_thinking`` keeps the model's own
    template flags (thinking budget, etc.)."""
    out = dict(base or {})
    for k, v in (over or {}).items():
        if k == "chat_template_kwargs" and isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _expand_env(value):
    """``${VAR}`` → environment value (empty + warning when unset)."""
    if isinstance(value, str):
        m = _ENV_REF.match(value.strip())
        if m:
            val = os.environ.get(m.group(1), "")
            if not val:
                log.debug("$%s is not set — header/value left empty", m.group(1))
            return val
    return value


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
    # StudioForge GPU lease: take the cards for the whole run so no co-tenant
    # (another StudioForge client, an OpenClaw agent's model) can be planned
    # onto them or evict ours. Needs the management PIN in ``headers``.
    lease: bool = False
    lease_devices: list | None = None
    lease_idle_ttl_s: float = studioforge.LEASE_IDLE_TTL_S
    wait_busy_s: float = studioforge.DEFAULT_WAIT_BUSY_S
    restore_residents: bool = True
    # remembers what /models returned (None = endpoint doesn't support listing)
    _models_cache: set | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _lease: dict | None = field(default=None, repr=False)
    _keepalive: threading.Thread | None = field(default=None, repr=False)
    _plans: dict = field(default_factory=dict, repr=False)
    _status_cache: tuple = field(default=(0.0, None), repr=False)

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
    def mgmt_headers(self) -> dict:
        """Headers for StudioForge management calls (the X-MCP-Pin lives here)."""
        return {k: v for k, v in self.headers.items() if k.lower() != "content-type"}

    def switch_model(self, model_id: str, context_length: int | None = None) -> float:
        """Make ``model_id`` the served model. Returns load seconds (0 for
        providers with no load step).

        StudioForge with ``lease: true``: take a GPU lease that loads the
        model onto the leased cards (evicting idle residents, waiting for busy
        ones), then make sure it serves the registry context. Without a
        lease: unload idle residents (never a serving one) and load at the
        registry context via load-recommended."""
        if self.type == "lmstudio":
            return lms.switch_model(model_id, context_length)
        if self.type == "studioforge":
            if self.lease:
                return self._lease_load(model_id, context_length)
            studioforge.unload_all(self.base_url, self.api_key, self.mgmt_headers(),
                                   wait_busy_s=self.wait_busy_s)
            t = studioforge.load_model(model_id, self.base_url, self.api_key,
                                       context_length, recommended=self.recommended_load,
                                       headers=self.mgmt_headers(), wait_busy_s=self.wait_busy_s)
            self._remember_plan(model_id)
            return t
        return 0.0

    # ------------------------------------------------------------ leases
    def _lease_load(self, model_id: str, context_length: int | None) -> float:
        t0 = time.perf_counter()
        hdrs = self.mgmt_headers()
        devices = list(self.lease_devices or studioforge.gpu_indices(self.base_url, self.api_key, hdrs))
        if self._lease and self._lease.get("_model_id") != model_id:
            self.release_lease()
        if self._lease is None:
            try:
                lease = studioforge.acquire_lease(
                    self.base_url, self.api_key, hdrs, devices, model_ids=[model_id],
                    reason=f"gauntlet benchmark: {model_id.rsplit('/', 1)[-1]}",
                    idle_ttl_s=self.lease_idle_ttl_s, wait_busy_s=self.wait_busy_s)
            except studioforge.StudioForgeError as e:
                if e.status in (401, 403, 404, 405):
                    log.warning("GPU lease unavailable (%s) — running WITHOUT a lease; "
                                "co-tenants can evict this model", e)
                    self.lease = False
                    return self.switch_model(model_id, context_length)
                raise
            lease["_model_id"] = model_id
            self._lease = lease
            atexit.register(self.release_lease)
            self._start_keepalive()
        # A lease names the model but its load is the server's own business:
        # it sizes slots itself (observed parallel=1) and a load that does not
        # fit fails SILENTLY (the lease stands, loaded[] stays empty — seen
        # with a foreign 20 GiB ComfyUI holder on one card). So load through
        # load-recommended at the registry context ourselves: it short-circuits
        # when the lease already produced a ready, multi-slot load at that
        # context, and otherwise answers with a structured 507 (retry_after_s /
        # per-mode suggestions) instead of silence.
        try:
            studioforge.wait_ready(model_id, self.base_url, self.api_key, headers=hdrs,
                                   never_appeared_grace_s=20.0, timeout_s=600)
        except studioforge.StudioForgeError as e:
            log.info("lease did not produce a ready %s (%s) — loading it explicitly",
                     model_id.rsplit("/", 1)[-1], str(e)[:120])
        studioforge.load_model(model_id, self.base_url, self.api_key, context_length,
                               recommended=self.recommended_load, headers=hdrs,
                               wait_busy_s=self.wait_busy_s)
        self._remember_plan(model_id)
        plan = self._plans.get(model_id) or {}
        log.info("%s serving under lease %s: parallel=%s ctx=%s devices=%s", model_id,
                 self._lease.get("_lease_id"), plan.get("parallel"), plan.get("ctx_size"),
                 plan.get("devices"))
        return time.perf_counter() - t0

    def _start_keepalive(self) -> None:
        if self._keepalive is not None or not self.lease_idle_ttl_s:
            return
        period = max(30.0, float(self.lease_idle_ttl_s) * _LEASE_TOUCH_FRACTION)

        def _loop():
            while True:
                time.sleep(period)
                lease = self._lease
                if lease is None:
                    return
                studioforge.touch_lease(self.base_url, self.api_key, self.mgmt_headers(),
                                        lease["_lease_id"])

        self._keepalive = threading.Thread(target=_loop, name="gauntlet-lease-keepalive",
                                           daemon=True)
        self._keepalive.start()

    def release_lease(self) -> None:
        """Release the standing GPU lease (idempotent; safe at exit)."""
        lease = self._lease
        if lease is None:
            return
        self._lease = None
        studioforge.release_lease(self.base_url, self.api_key, self.mgmt_headers(),
                                  lease["_lease_id"])

    def _remember_plan(self, model_id: str) -> None:
        try:
            live = studioforge.loaded_plan(model_id, self.base_url, self.api_key,
                                           self.mgmt_headers()) or {}
        except studioforge.StudioForgeError:
            live = {}
        self._plans[model_id] = {k: live.get(k) for k in
                                 ("state", "parallel", "ctx_size", "devices", "kv_cache_type",
                                  "loaded_by", "mode")}

    def loaded_plan_for(self, model_id: str) -> dict:
        """The placement this run measured under (recorded at load time)."""
        return dict(self._plans.get(model_id) or {})

    def live_context(self, model_id: str) -> int | None:
        ctx = (self._plans.get(model_id) or {}).get("ctx_size")
        try:
            return int(ctx) if ctx else None
        except (TypeError, ValueError):
            return None

    def workers(self, model_id: str) -> int:
        """How many requests to run at once for this model. openai: the
        configured concurrency. studioforge: the loaded model's parallel slot
        count, capped by the configured concurrency when one is set
        (``concurrency: auto`` = follow the server). lmstudio: 1."""
        if self.type == "openai":
            return self.concurrency
        if self.type == "studioforge":
            slots = studioforge.loaded_parallel(model_id, self.base_url, self.api_key,
                                                self.mgmt_headers())
            return min(slots, self.concurrency) if self.concurrency_explicit else slots
        return 1

    def _cached_status_plan(self, model_id: str) -> dict | None:
        """loaded_plan() with a short cache — called per case by the runner."""
        now = time.monotonic()
        with self._lock:
            ts, data = self._status_cache
            fresh = data is not None and now - ts < _STATUS_CACHE_S
        if not fresh:
            try:
                data = studioforge.status(self.base_url, self.api_key, self.mgmt_headers())
            except studioforge.StudioForgeError:
                return None  # unreadable status is not evidence of eviction
            with self._lock:
                self._status_cache = (now, data)
        for m in data.get("loaded", []):
            if m.get("model_id") == model_id:
                return {"state": m.get("state"), **(m.get("plan") or {})}
        return None

    def is_loaded(self, model_id: str) -> bool:
        """Eviction detection. StudioForge: the model must still be resident,
        ready, and on the SAME plan it was loaded with (an evicted model would
        otherwise be JIT-reloaded by the next request at planner defaults —
        different slots/context — and the run would silently continue under a
        placement its results are not labelled with)."""
        if self.type == "lmstudio":
            return lms.is_loaded(model_id)
        if self.type == "studioforge":
            want = self._plans.get(model_id)
            if not want:
                return True  # never loaded by us (pairwise/judge paths) — trust the server
            live = self._cached_status_plan(model_id)
            if live is None:
                return self._status_cache[1] is None  # unreadable status: assume fine
            if live.get("state") != "ready":
                return False
            for k in ("parallel", "ctx_size", "devices"):
                if want.get(k) is not None and live.get(k) != want.get(k):
                    log.warning("%s placement changed (%s: %s -> %s) — treating as evicted",
                                model_id, k, want.get(k), live.get(k))
                    return False
            return True
        return True  # stateless providers are always "loaded"

    def unload_all(self) -> None:
        if self.type == "lmstudio":
            lms.unload_all()
        elif self.type == "studioforge":
            studioforge.unload_all(self.base_url, self.api_key, self.mgmt_headers(),
                                   wait_busy_s=self.wait_busy_s)

    def loaded_models(self) -> list[dict]:
        if self.type == "lmstudio":
            return list(lms.loaded_models())
        if self.type == "studioforge":
            return studioforge.loaded_models(self.base_url, self.api_key)
        return []

    def snapshot(self):
        """State to restore after a run. LM Studio: the served model.
        StudioForge: the residents that were not ours, so a family bot's model
        can be brought back after the benchmark evicted it."""
        if self.type == "lmstudio":
            try:
                return lms.snapshot()
            except lms.LmsError:
                return []
        if self.type == "studioforge":
            try:
                return [r for r in studioforge.residents(self.base_url, self.api_key,
                                                         self.mgmt_headers())
                        if r.get("model_id")]
            except studioforge.StudioForgeError as e:
                log.warning("cannot snapshot StudioForge residents: %s", e)
                return []
        return None

    def restore(self, state) -> None:
        if self.type == "lmstudio" and state:
            lms.restore(state)
            return
        if self.type == "studioforge":
            self.release_lease()
            if not self.restore_residents:
                return
            before = {r["model_id"] for r in (state or [])}
            # models WE loaded that were not resident before: unload them so
            # the cards go back to whoever had them (a 97 GiB judge left
            # resident blocks the pin reconciler until its TTL)
            for mid in list(self._plans):
                if mid not in before:
                    try:
                        studioforge.unload_all(self.base_url, self.api_key, self.mgmt_headers(),
                                               wait_busy_s=60,
                                               keep=tuple(r for r in before))
                    except studioforge.StudioForgeError as e:
                        log.warning("unload of our %s failed: %s", mid, e)
                    break
            if not state:
                return
            try:
                now = {r["model_id"] for r in studioforge.residents(
                    self.base_url, self.api_key, self.mgmt_headers())}
            except studioforge.StudioForgeError:
                now = set()
            for r in state:
                mid = r["model_id"]
                if mid in now or str(r.get("loaded_by") or "").startswith("api:/api/leases"):
                    continue
                try:
                    # no body: the server re-plans from the model's own settings
                    # (device_override, ctx_size, ...) — the way it was resident
                    code, data = studioforge._mgmt(
                        "POST", self.base_url, self.api_key, self.mgmt_headers(),
                        f"/api/models/{studioforge._quote(mid)}/load", json={},
                        timeout=studioforge.WARMUP_TIMEOUT_S)
                    if code >= 400:
                        log.warning("restore of %s rejected (HTTP %d: %s)", mid, code,
                                    str(data)[:160])
                    else:
                        log.info("restored resident %s (was %s)", mid, r.get("loaded_by"))
                except studioforge.StudioForgeError as e:
                    log.warning("restore of %s failed: %s", mid, e)

    # ------------------------------------------------------------ chat
    def chat(self, model_id: str, messages: list[dict], **kw):
        """One streamed completion with retries (see api.stream_chat_retried)."""
        from .api import stream_chat_retried
        body_extra = merge_extra_body(self.extra_body, kw.pop("extra_body", None))
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
            # warned again, loudly, only when the provider is actually used
            # (preflight / judge selection) — a construction-time warning
            # for every provider on every command trained readers to ignore it
            log.debug("provider %s: $%s is not set", p.get("name"), env_name)
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
        headers={k: _expand_env(v) for k, v in (p.get("headers") or {}).items()},
        timeout=float(p.get("timeout", 900)),
        extra_body=dict(p.get("extra_body") or {}),
        lease=bool(p.get("lease", False)) and ptype == "studioforge",
        lease_devices=list(p["lease_devices"]) if p.get("lease_devices") else None,
        lease_idle_ttl_s=float(p.get("lease_idle_ttl_s", studioforge.LEASE_IDLE_TTL_S) or 0)
        or None,
        wait_busy_s=float(p.get("wait_busy_s", studioforge.DEFAULT_WAIT_BUSY_S)),
        restore_residents=bool(p.get("restore_residents", True)),
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
