"""Provider abstraction: where a model is served and how it is managed.

Three provider types cover every backend CrucibleForge talks to:

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
- ``anthropic`` — Anthropic's NATIVE Messages API (``/v1/messages``) with
  prompt caching (2026-11-11). Hosted like ``openai`` (no load management);
  the wire translation and its parity rules live in anthropic_native.py.

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
from urllib.parse import urlparse

import httpx

from . import lms, spend, studioforge
from .config import ConfigError, provider_of

log = logging.getLogger(__name__)

#: provider types that are a hosted API (no load management, optimistic
#: liveness, configured concurrency, no served-model check by default)
HOSTED_TYPES = ("openai", "anthropic")

#: set by ``run --allow-unpriced``: lets a hosted model with no ``price:`` be
#: called while a spend cap is on (otherwise it is refused, fail closed)
ALLOW_UNPRICED = False

#: Hosts that serve MiniMax's OpenAI-compatible API. Every model behind them
#: thinks, and MiniMax documents that the chain must be sent back: "the
#: complete model response (i.e., the assistant message) must be append to the
#: conversation history to maintain the continuity of the reasoning chain"
#: (platform.minimax.io/docs/api-reference/text-openai-api). Measured on the
#: bench's own cases: 1/10 recall with the chain dropped, 7/10 with it
#: replayed (2026-11-02 audit).
#:
#: A provider pointed at one of these hosts therefore gets the reasoning
#: replay by default. ``reasoning_replay: false`` in models.yaml turns it off
#: for that provider (and ``true`` turns it on anywhere else).
_MINIMAX_HOSTS = ("api.minimax.io", "api.minimaxi.com")


def _is_minimax(base_url: str) -> bool:
    """True when ``base_url`` points at MiniMax's OpenAI-compatible API."""
    host = (urlparse(base_url).hostname or "").lower()
    return host in _MINIMAX_HOSTS


def reasoning_replay_default(p: dict) -> bool:
    """The built-in default for a provider's reasoning replay: ON for
    MiniMax (whose API requires the chain back), OFF for everyone else --
    every other provider's request bytes stay exactly as they were."""
    if "reasoning_replay" in p:
        return bool(p["reasoning_replay"])
    return _is_minimax(str(p.get("base_url") or ""))

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



#: documented output maxima of the shipped hosted provider examples, by API host, used when neither the registry
#: nor the provider entry nor the /models listing states one (review 3, fix 3; read 2026-11-25): DeepSeek's Chat
#: Completions reference gives max_tokens 1..393,216 (384K) for its Flash model; MiniMax's OpenAI-compatible
#: reference gives max_completion_tokens up to 524,288 for MiniMax-M3.1-Flash and M3. A provider that refuses a
#: budget anyway is retried once at the limit its error states (runner._Ctx._chat).
HOSTED_OUTPUT_MAX = {"api.deepseek.com": 393216, "api.minimax.io": 524288, "api.minimaxi.com": 524288}

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
    rename_params: dict = field(default_factory=dict)
    drop_params: list = field(default_factory=list)
    recommended_load: bool = True
    concurrency_explicit: bool = False
    # StudioForge GPU lease: take the cards for the whole run so no co-tenant
    # (another StudioForge client, an OpenClaw agent's model) can be planned
    # onto them or evict ours. Needs the management PIN in ``headers``.
    lease: bool = False
    lease_devices: list | None = None
    lease_idle_ttl_s: float = studioforge.LEASE_IDLE_TTL_S
    wait_busy_s: float = studioforge.DEFAULT_WAIT_BUSY_S
    # The rig's planner sizes slots for one chat stream; a benchmark sends ~30
    # jobs at once. Below min_slots the load is widened toward target_slots at
    # the same ctx per slot (2026-09-24, JoyFox came up at parallel=1).
    min_slots: int = 4
    target_slots: int = 8
    restore_residents: bool = True
    # Resident fast path (WP-BENCH FIX-1): if the target model is ALREADY
    # resident, ready, and wide/multi-slot enough, switch_model() uses it
    # as-is — no lease, no unload_all, no PIN needed at all (loaded_plan is a
    # plain GET /api/status). Sizeable win for benching a pinned,
    # priority-tiered chat-tier model (e.g. a chat model pinned to specific
    # devices), which a lease can no longer touch anyway since D46 (see
    # RC-1/RC-7). Default on; set ``use_resident: false``
    # in models.yaml to always take the lease/unload path instead.
    use_resident: bool = True
    # Reasoning replay (MiniMax's documented protocol): the assistant turn is
    # sent back WITH its ``reasoning_content``, and every request carries
    # ``reasoning_split: true`` so the reasoning arrives in that channel for
    # every model on this provider. Set per provider in models.yaml
    # (``reasoning_replay: true``); the built-in default is ON for MiniMax
    # and OFF everywhere else -- see reasoning_replay_default().
    reasoning_replay: bool = False
    # a hosted judge's one retry after a cut verdict asks for at most this many output tokens (None = the built-in
    # JUDGE_LENGTH_RETRY_CAP); models.yaml ``judge_max_output_tokens`` on the provider entry
    judge_max_output_tokens: int | None = None
    # models.yaml ``max_output_tokens`` on the provider entry (review 3, fix 3): the provider's output maximum, one
    # number or ``{model_id: number}``; else the provider's /models listing when it states one, else
    # HOSTED_OUTPUT_MAX for a known host. Budgets above it are clamped and the row says so (budget_clamp)
    max_output_tokens: object = None
    # models.yaml ``hosted: true|false`` on the provider entry: the user's word on whether this server is a hosted
    # API (billed, judge spend apart) or a local one; None = by type and address (spend.is_hosted_at)
    hosted_override: bool | None = None
    # WP-BENCH FIX-2: force=true is never sent on this provider's own
    # initiative (see studioforge.acquire_lease) — only when a caller has
    # explicitly set this, itself only ever flipped by the CLI's
    # ``--force-evict`` flag, itself only ever meant to be passed on the maintainer's
    # explicit go-ahead (see the crucibleforge skill). Off by default.
    force_evict: bool = False
    # Bench-first policy (models.yaml, 2026-09-08 — see studioforge.py
    # "bench-first"): ``force_evict: true`` is the STANDING authorisation
    # (every run behaves as if --force-evict were passed);
    # ``lease_devices_preferred`` is tried first when the rig's planner says
    # the model fits there empty; ``render_vacate_mcp`` lets a foreign render
    # lease on the wanted cards be vacated (and resumed afterwards).
    force_evict_policy: bool = False
    lease_devices_preferred: list | None = None
    render_vacate_mcp: str | None = None
    # Bench-first: a resident that stays mid-request on the wanted cards for
    # longer than this is unloaded (its stream cut) — see
    # studioforge.acquire_lease(busy_unload_after_s). None = wait, then fail.
    busy_unload_after_s: float | None = None
    # The D46 tier every completion request declares (§12.2: 2 = agent).
    # ``bench-all --lease-mode day`` lowers it to 3 (background).
    request_priority: int = 2
    # ``bench-all --lease-mode day`` (MakoBench operator, 2026-11-06): a lease
    # names ONE card that no priority-1/2 or pinned model is recorded on, and
    # the run refuses rather than widen onto a protected card.
    day_single_card: bool = False
    # A SHARDED rig phase (judge --shard): other CrucibleForge processes have
    # their own models resident beside ours, so the end of this phase unloads
    # exactly the models THIS process loaded and restores nothing — the
    # "unload everything not resident before us" sweep would cut the other
    # shard's judge mid-request.
    own_unload_only: bool = False
    # model_id -> the registry ``price`` block of every model on this provider
    # (get_provider): Provider.chat prices each call for the spend cap/journal
    prices: dict = field(default_factory=dict, repr=False)
    # "body:<field>" / "header:<name>" / "off" / None (= by host, see
    # cache_route_for): the per-(model, case, repeat) prompt-cache routing key
    cache_route: str | None = None
    # "episode" (one key per (model, case, repeat)) / "call" (a fresh key per request) /
    # None = by host (cache_route_scope_for): xAI is "call", every other host "episode"
    cache_route_scope: str | None = None
    # KV cache type for every rig load (2026-11-13, lead): a recorded run setting, the same for
    # every model. None = the profile's run_settings.kv_cache_type, else the rig's choice.
    kv_cache_type: str | None = None
    # load on EXACTLY the leased cards (load_exact) instead of load-recommended, which plans across
    # the whole rig (2026-11-13: a [2,3] lease's model landed on [0,1]). Config `exact_on_lease: true`.
    exact_on_lease: bool = False
    # Wider placement (2026-11-13): below min_slots on the leased cards, ask
    # the rig's dry-run planner whether more cards hold clearly more slots at
    # the same ctx and move there. "thinking" (default) = only for models the
    # rig marks as thinking; "always"; "never".
    widen_devices: str = "thinking"
    _atexit_release: bool = field(default=False, repr=False)
    _vacated_render: bool = field(default=False, repr=False)
    # remembers what /models returned (None = endpoint doesn't support listing)
    _models_cache: set | None = field(default=None, repr=False)
    _route_max: dict = field(default_factory=dict, repr=False)
    _route_max_read: bool = field(default=False, repr=False)
    _route_max_warned: set = field(default_factory=set, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _lease: dict | None = field(default=None, repr=False)
    _keepalive: threading.Thread | None = field(default=None, repr=False)
    _plans: dict = field(default_factory=dict, repr=False)
    # True once this run loaded or leased anything on the rig: a run that
    # never touched it (hosted models only) restores nothing (2026-10-29: an
    # API-only run reloaded an embedder the nightly had evicted, at 02:37)
    _touched: bool = field(default=False, repr=False)
    _status_cache: tuple = field(default=(0.0, None), repr=False)
    # HTTP status of the last GET /models probe (None = no answer / not probed)
    last_probe_status: int | None = field(default=None, repr=False)
    # {model_id: price block} of the registry entries / judge candidates this
    # provider serves (spend.py: the spend cap prices each hosted call)
    prices: dict = field(default_factory=dict, repr=False)

    # ---------------------------------------------------------- discovery
    def auth_wall(self, probe: bool = False) -> int | None:
        """401/403 when the server last answered /models with an auth refusal
        (reachable, but it wants a key / rejected ours), else None. ``probe``
        issues a fresh GET /models when nothing has been recorded yet."""
        if self.last_probe_status is None and probe:
            self.alive()
        return self.last_probe_status if self.last_probe_status in (401, 403) else None

    def alive(self) -> bool:
        if self.type == "claude_code":
            import shutil
            return shutil.which("claude") is not None
        headers = self._headers()
        self.last_probe_status = None
        try:
            with httpx.Client(timeout=10) as http:
                resp = http.get(f"{self.base_url}/models", headers=headers)
                self.last_probe_status = resp.status_code
                if resp.status_code == 200:
                    return True
                # Some gateways 401/404 on /models but serve completions fine.
                # Treat "reachable but no listing" as alive for openai type.
                return self.type in HOSTED_TYPES and resp.status_code in (401, 403, 404, 405)
        except Exception:
            return False

    def _headers(self, client_name: str | None = None) -> dict:
        if self.type == "anthropic":
            from .anthropic_native import request_headers
            return request_headers(self.api_key, self.headers)
        h = {"Content-Type": "application/json", **self.headers}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self.type == "studioforge":
            # Rig-integration contract: every StudioForge
            # call names itself, using the same string a GPU lease would use
            # as its holder — "crucibleforge" for a run, "crucibleforge-judge"
            # for the judge phase (passed in by the caller).
            h.setdefault("X-SF-Client", client_name or studioforge.LEASE_HOLDER)
        return h

    def model_capabilities(self, model_id: str) -> set[str]:
        """What the server advertises for ``model_id`` (StudioForge:
        ``capabilities`` in /v1/models, e.g. {"tools", "thinking"}). Empty for
        other providers or an unknown model; a listing error propagates."""
        if self.type != "studioforge":
            return set()
        for m in studioforge.list_models_full(self.base_url, self.api_key):
            if m.get("id") == model_id:
                return {str(c) for c in (m.get("capabilities") or [])}
        return set()

    def list_models(self, refresh: bool = False) -> set[str]:
        """Model ids the provider says it serves. Empty set means "unknown /
        listing unsupported" — callers must not treat that as 'no models'."""
        with self._lock:
            if self._models_cache is not None and not refresh:
                return set(self._models_cache)
        ids: set[str] = set()
        if self.type == "claude_code":
            return ids          # no listing: the first call is the check
        if self.type == "lmstudio":
            try:
                ids = {m["modelKey"] for m in lms.list_models()}
            except lms.LmsError as e:
                log.warning("lms ls failed: %s", e)
        elif self.type == "studioforge":
            ids = set(studioforge.list_models(self.base_url, self.api_key))
        else:
            try:
                self.last_probe_status = None
                with httpx.Client(timeout=15) as http:
                    resp = http.get(f"{self.base_url}/models", headers=self._headers())
                self.last_probe_status = resp.status_code
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("data", data) if isinstance(data, dict) else data
                    for m in items or []:
                        mid = m.get("id") if isinstance(m, dict) else m
                        if mid:
                            ids.add(str(mid))
                            # 4.5.1 (PLAN-451-LIMITS step 1c): a route's own output maximum, when it states one
                            top = (m.get("top_provider") or {}) if isinstance(m, dict) else {}
                            mx = top.get("max_completion_tokens") if isinstance(top, dict) else None
                            if not mx and isinstance(m, dict):
                                # another hosted API that states its output maximum in its listing (review 3, fix 3)
                                mx = next((m.get(k) for k in ("max_output_tokens", "max_completion_tokens",
                                                              "output_token_limit") if isinstance(m.get(k), int)),
                                          None)
                            if isinstance(mx, int) and mx > 0:
                                self._route_max[str(mid)] = mx
            except Exception as e:
                log.debug("%s: /models listing failed: %s", self.name, e)
        with self._lock:
            self._models_cache = set(ids)
        return ids

    def route_max_output(self, model_id: str) -> int | None:
        """The provider's output maximum for ``model_id``, or None (no clamp): the provider entry's
        ``max_output_tokens`` (a number or ``{model_id: number}``), else what the /models listing states for it
        (OpenRouter ``top_provider.max_completion_tokens``; another hosted API's ``max_output_tokens`` /
        ``max_completion_tokens`` / ``output_token_limit``), else :data:`HOSTED_OUTPUT_MAX` for the host. Rig
        providers: None. :meth:`route_max_source` says where the number came from."""
        mx, self._route_max_src = self._route_max_lookup(model_id)
        return mx

    def route_max_source(self) -> str | None:
        return getattr(self, "_route_max_src", None)

    def _route_max_lookup(self, model_id: str) -> tuple[int | None, str | None]:
        from urllib.parse import urlparse
        if self.type not in HOSTED_TYPES or not self.is_hosted:
            return None, None                   # a local server (a rig behind an OpenAI-shape API) has no route max
        conf = self.max_output_tokens
        if isinstance(conf, dict):
            conf = conf.get(model_id)
        if conf:
            return int(conf), "provider config"
        host = (urlparse(self.base_url or "").hostname or "").lower()
        if host == "openrouter.ai" and not self._route_max_read:
            self._route_max_read = True
            self.list_models(refresh=True)
        # another API: its listing is read by the run's availability check (is_available), never fetched here only
        # for this; the runner asks again after that check
        if model_id in self._route_max:
            return int(self._route_max[model_id]), "provider listing"
        if host == "openrouter.ai":
            # review 3 note 8: an id that matches no route left the clamp silently off; say so, loudly, once
            if model_id not in self._route_max_warned:
                self._route_max_warned.add(model_id)
                listed = model_id in (self._models_cache or set())
                log.warning("!!! %s: OpenRouter's /models listing %s %r: no output maximum known, budgets are NOT "
                            "clamped (set max_output_tokens on the model or provider entry)", self.name,
                            "states no output maximum for" if listed else "has no route for", model_id)
            return None, None
        if host in HOSTED_OUTPUT_MAX:
            return int(HOSTED_OUTPUT_MAX[host]), "documented default"
        return None, None

    def is_available(self, model_id: str) -> bool:
        ids = self.list_models()
        if not ids:
            # listing unsupported: optimistic — the wrong-model / transport
            # errors on the first call are the real check
            return self.type in HOSTED_TYPES or self.type == "claude_code"
        return model_id in ids

    # ------------------------------------------------------ load control
    def mgmt_headers(self, client_name: str | None = None) -> dict:
        """Headers for StudioForge management calls (the X-MCP-Pin lives
        here). ``client_name`` lets a caller (e.g. the judge phase) declare
        itself under a different identity than the run's own — see
        ``_headers`` and the rig-integration contract."""
        h = {k: v for k, v in self.headers.items() if k.lower() != "content-type"}
        if self.type == "studioforge":
            h.setdefault("X-SF-Client", client_name or studioforge.LEASE_HOLDER)
        return h

    def switch_model(self, model_id: str, context_length: int | None = None) -> float:
        """Make ``model_id`` the served model. Returns load seconds (0 for
        providers with no load step, and for the resident fast path below).

        StudioForge, resident fast path (``use_resident``, default on):
        if ``model_id`` is already resident, ``ready``, multi-slot, and at
        least as wide as ``context_length``, use it as-is — no lease taken,
        nothing unloaded, no PIN required (this is a plain GET /api/status).
        This is what lets a bench run against a pinned, priority-tiered
        chat-tier model (e.g. a pinned priority-1 chat model)
        without touching the pin at all — see RC-1/RC-7.

        Otherwise, with ``lease: true``: take a GPU lease that loads the
        model onto the leased cards (evicting idle residents, waiting for busy
        ones), then make sure it serves the registry context. Without a
        lease: unload idle residents (never a serving one) and load at the
        registry context via load-recommended."""
        self._touched = True
        if self.type == "lmstudio":
            return lms.switch_model(model_id, context_length)
        if self.type == "studioforge":
            if self.use_resident and self._resident_ready(model_id, context_length):
                live = self._plans.get(model_id) or {}
                log.info("%s already resident, ready, parallel=%s ctx=%s — using it as-is "
                        "(no lease, no unload; WP-BENCH FIX-1 resident fast path)",
                        model_id, live.get("parallel"), live.get("ctx_size"))
                return 0.0
            if self.lease:
                return self._lease_load(model_id, context_length)
            if self.day_single_card:
                raise studioforge.DayCardRefused(
                    "day mode needs a GPU lease (lease: true) — refused rather than "
                    "unloading residents", status=409)
            studioforge.unload_all(self.base_url, self.api_key, self.mgmt_headers(),
                                   wait_busy_s=self.wait_busy_s)
            t = studioforge.load_model(model_id, self.base_url, self.api_key,
                                       context_length, recommended=self.recommended_load,
                                       headers=self.mgmt_headers(), wait_busy_s=self.wait_busy_s,
                                       min_slots=self.min_slots, target_slots=self.target_slots)
            self._remember_plan(model_id)
            self._enforce_kv(model_id, context_length)
            return t
        return 0.0

    def _resident_ready(self, model_id: str, context_length: int | None) -> bool:
        """True when ``model_id`` is already resident, ``ready``, multi-slot,
        and wide enough that ``switch_model`` has nothing to do. Mirrors the
        identical check already inside ``studioforge.load_model`` one layer
        down (it short-circuits the same way once a lease/unload already got
        it there) — this just makes the same fact usable BEFORE a lease or
        unload is even attempted. Recorded into ``self._plans`` on a hit so
        eviction detection (``is_loaded``) and ``meta.plan`` work exactly as
        if this provider had loaded it itself."""
        wanted = int(context_length or 32768)
        try:
            live = studioforge.loaded_plan(model_id, self.base_url, self.api_key,
                                           self.mgmt_headers())
        except studioforge.StatusUnavailable:
            return False
        ready = bool(live and live.get("state") == "ready"
                    and int(live.get("parallel") or 1) > 1
                    and int(live.get("ctx_size") or 0) >= wanted)
        if ready:
            if self.lease:
                studioforge.release_stale_leases(self.base_url, self.api_key,
                                                 self.mgmt_headers(), studioforge.LEASE_HOLDER,
                                                 model_id, (self._lease or {}).get("_lease_id"))
            self._refuse_other_bench_lease(model_id, live)
            self._remember_plan(model_id, live=live)
        return ready

    def _refuse_other_bench_lease(self, model_id: str, live: dict | None) -> None:
        """One benchmark at a time: a resident that ANOTHER CrucibleForge run
        leased (holder ``crucibleforge*``, not our own lease) is that run's
        model under measurement — benching on it would share its slots and
        corrupt both runs' timings. Raise instead of using it."""
        try:
            leases = studioforge.list_leases(self.base_url, self.api_key, self.mgmt_headers())
        except studioforge.StudioForgeError:
            return  # no lease API (older rig / no PIN): nothing to check against
        ours = (self._lease or {}).get("_lease_id")
        devices = set(int(d) for d in ((live or {}).get("devices") or []))
        for lease in leases:
            holder = str(lease.get("holder") or "")
            lid = str(lease.get("id") or lease.get("lease_id") or "")
            if not holder.startswith(studioforge.LEASE_HOLDER) or (ours and lid == ours):
                continue
            names_it = model_id in (lease.get("model_ids") or [])
            overlaps = bool(devices & set(int(d) for d in (lease.get("devices") or [])))
            if names_it or overlaps:
                raise studioforge.StudioForgeError(
                    f"{model_id.rsplit('/', 1)[-1]} is held by another CrucibleForge run "
                    f"(lease {lid or '?'} holder {holder!r} on devices {lease.get('devices')}) "
                    f"— one benchmark at a time; retry when it finishes", status=409)

    # ------------------------------------------------------------ leases
    def _lease_load(self, model_id: str, context_length: int | None) -> float:
        t0 = time.perf_counter()
        hdrs = self.mgmt_headers()
        if self._lease and self._lease.get("_model_id") != model_id:
            self.release_lease()
        if self._lease is None:
            # a crashed run's lease for this model (STEP4: 92fa55704e1e) is
            # released first, never stacked under a second one
            studioforge.release_stale_leases(self.base_url, self.api_key, hdrs,
                                             studioforge.LEASE_HOLDER, model_id)
            devices = self.claim_devices(model_id)
            try:
                self._acquire(model_id, devices)
            except studioforge.StudioForgeError as e:
                if e.status in (401, 403, 404, 405) and self.day_single_card:
                    # the lease-less path unloads idle residents: never by day
                    raise studioforge.DayCardRefused(
                        f"day mode: no GPU lease available ({e}) — refused rather than "
                        f"unloading residents", status=409) from e
                if e.status in (401, 403, 404, 405):
                    log.warning("GPU lease unavailable (%s) — running WITHOUT a lease; "
                                "co-tenants can evict this model", e)
                    self.lease = False
                    return self.switch_model(model_id, context_length)
                raise
        self._load_under_lease(model_id, context_length)
        if self._wants_wider_devices(model_id):
            self._widen_devices(model_id, context_length)
        plan = self._plans.get(model_id) or {}
        log.info("%s serving under lease %s: parallel=%s ctx=%s devices=%s", model_id,
                 self._lease.get("_lease_id"), plan.get("parallel"), plan.get("ctx_size"),
                 plan.get("devices"))
        return time.perf_counter() - t0

    def _acquire(self, model_id: str, devices: list[int]) -> dict:
        """Take the run's GPU lease on ``devices`` (the provider's force /
        busy-unload policy) and start its keepalive."""
        lease = studioforge.acquire_lease(
            self.base_url, self.api_key, self.mgmt_headers(), devices, model_ids=[model_id],
            reason=f"crucibleforge benchmark: {model_id.rsplit('/', 1)[-1]}",
            idle_ttl_s=self.lease_idle_ttl_s, wait_busy_s=self.wait_busy_s,
            force=self.force_evict and not self.day_single_card,
            busy_unload_after_s=(self.busy_unload_after_s
                                 if self.force_evict and not self.day_single_card
                                 else None))
        lease["_model_id"] = model_id
        lease["_devices"] = [int(d) for d in devices]
        self._lease = lease
        # a lease can evict residents (force_evict): they are restored
        # even when the phase fails before any load (review S5)
        self._touched = True
        if not self._atexit_release:
            atexit.register(self.release_lease)
            self._atexit_release = True
        self._start_keepalive()
        return lease

    def _load_under_lease(self, model_id: str, context_length: int | None) -> None:
        # A lease names the model but its load is the server's own business:
        # it sizes slots itself (observed parallel=1) and a load that does not
        # fit fails SILENTLY (the lease stands, loaded[] stays empty — seen
        # with a foreign 20 GiB ComfyUI holder on one card). So load through
        # load-recommended at the registry context ourselves: it short-circuits
        # when the lease already produced a ready, multi-slot load at that
        # context, and otherwise answers with a structured 507 (retry_after_s /
        # per-mode suggestions) instead of silence.
        hdrs = self.mgmt_headers()
        try:
            # poll (every READY_POLL_S = 2 s): a lease that is loading the
            # model is waited for; one that did not start a load is detected
            # on the first poll instead of after a fixed 20 s
            studioforge.wait_ready(model_id, self.base_url, self.api_key, headers=hdrs,
                                   never_appeared_grace_s=studioforge.READY_POLL_S,
                                   timeout_s=600)
        except studioforge.StudioForgeError as e:
            log.info("lease did not produce a ready %s (%s) — loading it explicitly",
                     model_id.rsplit("/", 1)[-1], str(e)[:120])
        leased = sorted(int(d) for d in (self._lease or {}).get("_devices") or [])
        if self.exact_on_lease and leased:
            # A provider restricted to named cards (lease_devices) loads on EXACTLY the leased
            # cards: load-recommended plans across the whole rig and placed a [2,3] lease's model
            # on [0,1] (2026-11-13 23:11, mblab-rigtput-d). The widest slot count up to
            # target_slots that fits at the registry context, with the run's KV cache type.
            ctx = int(context_length or 32768)
            live = studioforge.load_exact(model_id, self.base_url, self.api_key, hdrs, ctx,
                                          range(max(1, int(self.target_slots or 1)), 0, -1),
                                          devices=leased, kv=self.wanted_kv())
            self._remember_plan(model_id, live=live)
        else:
            studioforge.load_model(model_id, self.base_url, self.api_key, context_length,
                                   recommended=self.recommended_load, headers=hdrs,
                                   wait_busy_s=self.wait_busy_s,
                                   min_slots=self.min_slots, target_slots=self.target_slots)
            self._remember_plan(model_id)
        self._check_inside_lease(model_id)
        self._enforce_kv(model_id, context_length)
        self._check_inside_lease(model_id)

    def _check_inside_lease(self, model_id: str) -> None:
        """Fail closed when the model is serving on a card outside its lease (a restricted
        provider, lease_devices set): unload it and raise, never run there."""
        leased = {int(d) for d in (self._lease or {}).get("_devices") or []}
        if not (self.lease_devices and leased):
            return
        devs = {int(d) for d in (self._plans.get(model_id) or {}).get("devices") or []}
        if devs and not devs <= leased:
            log.error("%s is serving on %s, outside its lease %s — unloading, refusing to run",
                      model_id, sorted(devs), sorted(leased))
            try:
                studioforge.unload_model(self.base_url, self.api_key, self.mgmt_headers(), model_id)
            finally:
                raise studioforge.StudioForgeError(
                    f"{model_id}: served on {sorted(devs)}, outside the lease {sorted(leased)}",
                    status=409)

    def wanted_kv(self) -> str | None:
        """The KV cache type every load must use: the provider's ``kv_cache_type``, else the
        active profile's ``run_settings.kv_cache_type``, else None (the rig decides)."""
        if self.kv_cache_type:
            return self.kv_cache_type
        try:
            from . import profiles
            name = profiles.active_name()
            prof = profiles.load_profile(name) if name else None
        except Exception:  # noqa: BLE001 - no profile = no setting
            return None
        kv = ((prof or {}).get("run_settings") or {}).get("kv_cache_type")
        return str(kv).strip().lower() or None if kv else None

    def _enforce_kv(self, model_id: str, context_length: int | None) -> None:
        """Reload at the same ctx per slot and on the same cards with the wanted KV cache type
        when the live load differs (the widest slot count up to target_slots that fits)."""
        kv = self.wanted_kv()
        if not kv or self.type != "studioforge":
            return
        live = dict(self._plans.get(model_id) or {})
        if str(live.get("kv_cache_type") or "").lower() == kv and str(
                live.get("kv_cache_type_v") or kv).lower() == kv:
            return
        ctx = int(live.get("ctx_size") or context_length or 32768)
        devices = live.get("devices") or (self._lease or {}).get("_devices")
        top = max(int(self.target_slots or 1), int(live.get("parallel") or 1))
        log.warning("%s: loaded with KV %s, the run setting is %s — reloading at ctx %d on %s",
                    model_id, live.get("kv_cache_type"), kv, ctx, devices)
        new = studioforge.load_exact(model_id, self.base_url, self.api_key, self.mgmt_headers(),
                                     ctx, range(top, 0, -1), devices=devices, kv=kv)
        self._remember_plan(model_id, live=new)

    # ------------------------------------------- wider placement (2026-11-13)
    def _wants_wider_devices(self, model_id: str) -> bool:
        """Below ``min_slots`` on the leased cards, for a model the policy
        covers (``widen_devices``: ``thinking`` = models the rig marks as
        thinking -- long outputs that queue badly; ``always``; ``never``).
        Never in day mode (one card, by contract)."""
        if self.day_single_card or self.widen_devices == "never" or not self.min_slots:
            return False
        plan = self._plans.get(model_id) or {}
        if not plan.get("parallel") or int(plan["parallel"]) >= self.min_slots:
            return False      # wide enough, or no live plan to judge from
        leased = set((self._lease or {}).get("_devices") or ())
        if self.lease_devices and set(int(d) for d in self.lease_devices) <= leased:
            return False      # the lease already names every card the config allows
        if self.widen_devices == "thinking":
            try:
                return "thinking" in self.model_capabilities(model_id)
            except Exception as e:  # noqa: BLE001 — a listing error just skips the move
                log.info("capabilities of %s unreadable (%s) — not widening onto more cards",
                         model_id, e)
                return False
        return True

    def _spare_cards(self, model_id: str, current: list[int]) -> list[int]:
        """Rig cards a wider placement may ADD: in ``lease_devices`` (else
        every card), not leased by anyone else (a render lease included --
        widening never vacates), and with no other model resident (widening
        never evicts: the memory embedder or a chat model there stays)."""
        hdrs = self.mgmt_headers()
        pool = (list(self.lease_devices) if self.lease_devices
                else studioforge.gpu_indices(self.base_url, self.api_key, hdrs))
        held = studioforge.foreign_held_devices(
            studioforge.list_leases(self.base_url, self.api_key, hdrs), vacatable_render=False)
        occupied: set[int] = set()
        for m in studioforge.status(self.base_url, self.api_key, hdrs).get("loaded") or []:
            if m.get("model_id") != model_id:
                occupied.update(int(d) for d in ((m.get("plan") or {}).get("devices") or []))
        return [int(d) for d in pool
                if int(d) not in current and int(d) not in held and int(d) not in occupied]

    def _wider_choice(self, model_id: str, ctx: int, live: dict) -> dict | None:
        """Ask the rig's dry-run planner what every wider card set would hold
        at the same ctx per slot and pick one (studioforge.choose_wider_placement)."""
        from itertools import combinations
        hdrs = self.mgmt_headers()
        current = sorted(int(d) for d in (live.get("devices")
                                          or (self._lease or {}).get("_devices") or []))
        have = int(live.get("parallel") or 1)
        spare = self._spare_cards(model_id, current)
        if not spare:
            log.info("%s: %d slot(s) on %s and no spare card to widen onto", model_id, have,
                     current)
            return None
        speeds = studioforge.placement_speeds(self.base_url, self.api_key, hdrs, model_id)
        base_tps = studioforge.per_slot_speed(speeds, current)
        if base_tps is None:
            log.info("%s: no speed estimate for %s — not widening", model_id, current)
            return None
        ours = studioforge.own_vram_bytes(self.base_url, self.api_key, hdrs, model_id)
        kv_live = str(live.get("kv_cache_type") or "f16").lower()
        kvs = [kv_live] + (["q8_0"] if kv_live not in studioforge._KV_Q8_OR_SMALLER else [])
        widths = range(have + 1, max(self.target_slots, have + 1) + 1)
        cands = []
        for k in range(1, len(spare) + 1):
            for extra in combinations(spare, k):
                devs = sorted(current + list(extra))
                tps = studioforge.per_slot_speed(speeds, devs)
                if tps is None:
                    continue
                credit = sum(ours.get(d, 0) for d in devs)
                best = {"devices": devs, "slots": 0, "kv": kv_live, "tps": tps}
                for kv in kvs:
                    n = studioforge.max_slots_dry_run(self.base_url, self.api_key, hdrs,
                                                      model_id, devs, ctx, kv, widths, credit)
                    if n > best["slots"]:
                        best.update(slots=n, kv=kv)
                log.info("%s: dry run on %s — %d slot(s) at ctx %d (KV %s), ~%.1f tok/s "
                         "per stream", model_id, devs, best["slots"], ctx, best["kv"], tps)
                cands.append(best)
        return studioforge.choose_wider_placement(
            {"devices": current, "slots": have, "tps": base_tps}, cands,
            cap_slots=self.target_slots)

    def _widen_devices(self, model_id: str, context_length: int | None) -> None:
        """Move the run onto a wider card set when the dry run promises a
        clear throughput gain (see studioforge "wider placement"). Same ctx
        per slot, same budgets; only placement, slot count and (as the
        existing slot widening already allows) a q8_0 KV cache can change.
        Any failure puts the model back on the cards it had."""
        hdrs = self.mgmt_headers()
        live = dict(self._plans.get(model_id) or {})
        ctx = int(live.get("ctx_size") or context_length or 32768)
        have = int(live.get("parallel") or 1)
        old_devices = sorted(int(d) for d in (live.get("devices")
                                              or (self._lease or {}).get("_devices") or []))
        try:
            choice = self._wider_choice(model_id, ctx, live)
        except studioforge.StudioForgeError as e:
            log.warning("%s: wider placement check failed (%s) — staying on %s", model_id, e,
                        old_devices)
            return
        if choice is None:
            log.info("%s: no wider card set beats %d slot(s) on %s by %.1fx — staying",
                     model_id, have, old_devices, studioforge.WIDEN_DEVICES_MIN_GAIN)
            return
        log.warning("%s: only %d slot(s) on %s — moving to %s for an estimated %d slot(s) at "
                    "ctx %d (KV %s), ~%.1fx the aggregate throughput", model_id, have,
                    old_devices, choice["devices"], choice["slots"], ctx, choice["kv"],
                    choice["gain"])
        new = None
        try:
            studioforge.unload_model(self.base_url, self.api_key, hdrs, model_id)
            self._drop_lease_only()
            self._acquire(model_id, choice["devices"])
            try:
                studioforge.wait_ready(model_id, self.base_url, self.api_key, headers=hdrs,
                                       never_appeared_grace_s=studioforge.READY_POLL_S,
                                       timeout_s=600)
            except studioforge.StudioForgeError:
                pass  # _try_widths (re)loads it explicitly either way
            body = {"ctx_size": ctx, "devices": list(choice["devices"]),
                    "kv_cache_type": choice["kv"], "kv_cache_type_v": choice["kv"]}
            new = studioforge._try_widths(model_id, self.base_url, self.api_key, hdrs, body,
                                          list(range(int(choice["slots"]), have, -1)), have)
        except studioforge.StudioForgeError as e:
            log.warning("%s: moving to %s failed (%s)", model_id, choice["devices"], e)
        if new is not None:
            self._remember_plan(model_id, live=new)
            return
        log.warning("%s: the wider placement did not come up — back to %s", model_id,
                    old_devices)
        if self._lease is not None:
            studioforge.unload_model(self.base_url, self.api_key, hdrs, model_id)
            self._drop_lease_only()
        self._acquire(model_id, old_devices)
        self._load_under_lease(model_id, context_length)

    def _drop_lease_only(self) -> None:
        """Release the GPU lease WITHOUT handing a vacated render tenant its
        card back (the run is only re-leasing, not ending)."""
        lease, self._lease = self._lease, None
        if lease is not None:
            studioforge.release_lease(self.base_url, self.api_key,
                                      # the identity that took it (the rig refuses another holder)
                                      self.mgmt_headers(client_name=lease.get("_holder")),
                                      lease["_lease_id"])

    def _start_keepalive(self) -> None:
        # a re-lease (wider placement) can outlive a keepalive that saw no
        # lease for a moment and exited: start a fresh one then
        if ((self._keepalive is not None and self._keepalive.is_alive())
                or not self.lease_idle_ttl_s):
            return
        period = max(30.0, float(self.lease_idle_ttl_s) * _LEASE_TOUCH_FRACTION)

        def _loop():
            while True:
                time.sleep(period)
                lease = self._lease
                if lease is None:
                    return
                studioforge.touch_lease(self.base_url, self.api_key,
                                        self.mgmt_headers(client_name=lease.get("_holder")),
                                        lease["_lease_id"])

        self._keepalive = threading.Thread(target=_loop, name="crucibleforge-lease-keepalive",
                                           daemon=True)
        self._keepalive.start()

    def claim_devices(self, model_id: str, exclude=()) -> list[int]:
        """The cards a lease for ``model_id`` should name, bench-first style:
        ``lease_devices_preferred`` when the planner says the model fits
        there empty, else ``lease_devices`` (or every card). Then, if a
        foreign RENDER lease stands on those cards, vacate it through
        the image server and remember to resume it on release. ``exclude``: cards
        never named, whatever the config or the planner says (the judge's
        GPU 3 rule, judge.JUDGE_NEVER_DEVICES)."""
        exclude = {int(d) for d in exclude or ()}
        hdrs = self.mgmt_headers()
        if self.day_single_card:
            # day mode: never vacate a render lease, never widen (no
            # preferred/fallback walk), one unprotected card or a refusal
            return studioforge.choose_day_card(self.base_url, self.api_key, hdrs, model_id,
                                               exclude=exclude)
        try:
            busy = studioforge.foreign_held_devices(
                studioforge.list_leases(self.base_url, self.api_key, hdrs),
                vacatable_render=bool(self.render_vacate_mcp))
        except studioforge.StudioForgeError as e:
            log.warning("lease list unavailable (%s) — cannot see other holders' cards", e)
            busy = set()
        if self.lease_devices:
            fallback = list(self.lease_devices)
        else:
            try:
                fallback = studioforge.gpu_indices(self.base_url, self.api_key, hdrs)
            except studioforge.StudioForgeError:
                if not self.lease_devices_preferred:
                    raise
                fallback = list(self.lease_devices_preferred)
        preferred = self.lease_devices_preferred
        if exclude:
            fallback = [d for d in fallback if int(d) not in exclude]
            if preferred:
                preferred = [d for d in preferred if int(d) not in exclude] or None
            if not fallback:
                raise studioforge.StudioForgeError(
                    f"no card left for {model_id.rsplit('/', 1)[-1]} once {sorted(exclude)} "
                    f"are excluded", status=409)
        devices = studioforge.choose_lease_devices(self.base_url, self.api_key, hdrs, model_id,
                                                   preferred, fallback, busy=busy)
        devices = [d for d in devices if int(d) not in exclude]
        if studioforge.vacate_render_leases(self.base_url, self.api_key, hdrs, devices,
                                            self.render_vacate_mcp):
            self._vacated_render = True
        return devices

    def release_lease(self) -> None:
        """Release the standing GPU lease (idempotent; safe at exit), then
        hand the render tenant its card back if we vacated it."""
        lease = self._lease
        if lease is not None:
            self._lease = None
            studioforge.release_lease(self.base_url, self.api_key,
                                      # the identity that took it (the rig refuses another holder)
                                      self.mgmt_headers(client_name=lease.get("_holder")),
                                      lease["_lease_id"])
        if self._vacated_render:
            self._vacated_render = False
            try:
                studioforge.render_server_control(self.render_vacate_mcp, "resume", timeout=60)
                log.info("image server resumed (render lease handed back)")
            except studioforge.StudioForgeError as e:
                log.warning("image server resume failed — image generation stays vacated "
                            "until someone runs comfy_control(resume): %s", e)

    def _remember_plan(self, model_id: str, live: dict | None = None) -> None:
        """Record the live plan for ``model_id``. Pass ``live`` when the
        caller already has a fresh one (WP-BENCH review M4 — the resident
        fast path used to fetch it once for its own ready-check and again
        here, two ``GET /api/status`` round trips for one ``switch_model``
        call); omitted, this fetches it itself as before."""
        if live is None:
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

    def trained_context(self, model_id: str) -> int | None:
        """The window ``model_id`` was trained on, as the rig publishes it
        (StudioForge ``/v1/models`` → ``studioforge.n_ctx_train``). The rig
        refuses a load past it (HTTP 400 "is trained to N tokens"), so the
        runner never asks for more. ``None`` = unknown (other providers, no
        metadata, listing failed) — the caller keeps its context."""
        if self.type != "studioforge":
            return None
        try:
            records = studioforge.list_models_full(self.base_url, self.api_key)
        except Exception as e:  # noqa: BLE001 — unknown, not fatal: the load is the real check
            log.debug("%s: trained window of %s unavailable: %s", self.name, model_id, e)
            return None
        for m in records or []:
            if m.get("id") == model_id:
                n = (m.get("studioforge") or {}).get("n_ctx_train")
                try:
                    return int(n) if n and int(n) > 0 else None
                except (TypeError, ValueError):
                    return None
        return None

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
        if self.type in HOSTED_TYPES:
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

    def _placement_guard(self, model_id: str):
        """Before EVERY send of an exact-on-lease StudioForge model (first try and each
        transport retry): the model must be ready on exactly the plan we loaded (devices,
        ctx, slots). A request to an id that is not loaded makes the rig JIT-load it at its
        planner defaults (2026-11-14 02:10: ctx 262,144 x 1 slot, outside our lease) and the
        row would carry a placement it is not labelled with. So: fail closed, no request is
        sent; the job halts with an error row (no verdict) and the runner's eviction check
        reloads exactly before the next job. Unreadable status also fails closed.
        None when the guard does not apply (no lease restriction / never loaded by us)."""
        if self.type != "studioforge" or not self.exact_on_lease or not self.lease_devices:
            return None
        want = self._plans.get(model_id)
        if not want:
            return None

        def _guard() -> None:
            try:
                live = studioforge.loaded_plan(model_id, self.base_url, self.api_key,
                                               self.mgmt_headers())
            except studioforge.StudioForgeError as e:
                raise studioforge.StudioForgeError(
                    f"{model_id}: placement unverifiable ({e}); request not sent", status=409) from e
            bad = None
            if not live:
                bad = "not loaded (a request would JIT-load it at planner defaults)"
            elif live.get("state") != "ready":
                bad = f"state {live.get('state')!r}"
            else:
                for k in ("devices", "ctx_size", "parallel"):
                    w, l_ = want.get(k), live.get(k)
                    if k == "devices" and w is not None and l_ is not None:
                        w, l_ = sorted(int(d) for d in w), sorted(int(d) for d in l_)
                    if w is not None and l_ != w:
                        bad = f"{k} {l_} != loaded {w}"
                        break
            if bad:
                log.error("%s: placement lost before a send (%s) — request not sent", model_id, bad)
                raise studioforge.StudioForgeError(
                    f"{model_id}: placement lost ({bad}); request not sent", status=409)
        return _guard

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
        StudioForge: the residents that were not ours, so a chat-tier model
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
            if not self.restore_residents or not self._touched:
                return
            from . import stopflag
            if stopflag.ABORT.is_set():
                # stopping (SIGTERM): reloading the residents is many slow
                # calls to a rig that may not even answer (the stop that hung,
                # 2026-10-28) — the rig's own pin reconciler brings pinned
                # residents back. But the models THIS run loaded are unloaded
                # (one short call each, no waiting for idle: the run is over):
                # a stopped bench-all left its model resident and the next
                # judge lease was refused 409 (2026-10-31).
                before = {r["model_id"] for r in (state or [])}
                ours = [m for m in self._plans if m not in before]
                if ours:
                    studioforge.unload_models(self.base_url, self.api_key,
                                              self.mgmt_headers(), ours)
                log.warning("stopping — %s residents are not restored by this run", self.name)
                return
            before = {r["model_id"] for r in (state or [])}
            # models WE loaded that were not resident before: unload them so
            # the cards go back to whoever had them (a 97 GiB judge left
            # resident blocks the pin reconciler until its TTL)
            if self.day_single_card or self.own_unload_only:
                # day mode / a shard: unload exactly the models this run
                # loaded — never a resident someone else loaded while we ran
                ours = [m for m in self._plans if m not in before]
                if ours:
                    studioforge.unload_models(self.base_url, self.api_key,
                                              self.mgmt_headers(), ours)
                if self.own_unload_only:
                    return
            for mid in ([] if self.day_single_card else list(self._plans)):
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

    @property
    def is_hosted(self) -> bool:
        """A hosted API: the entry's ``hosted:`` when set, else by type and address (a local server is local)."""
        if self.hosted_override is not None:
            return self.hosted_override
        return spend.is_hosted_at(self.type, self.base_url)

    # ------------------------------------------------------------ chat
    def chat(self, model_id: str, messages: list[dict], *,
             client_name: str | None = None, **kw):
        """One chat call. A call through the hosted judge's own provider entry runs as a judge call
        (spend.judge_role): booked apart from every model's cost and caps (review F3: by role, never by id).
        A hosted judge call also gets the provider's own thinking-off switch (:func:`judge_thinking_off`) and,
        when it is cut at its output ceiling with no complete JSON object, ONE more try at a larger budget
        (:func:`judge_length_retry_tokens`). A local judge's calls are sent exactly as before."""
        with spend.judge_role(self.name):
            hosted_judge = spend.is_judge() and self.is_hosted
            if hosted_judge:
                kw["extra_body"] = judge_thinking_off(self.base_url, merge_extra_body(self.extra_body,
                                                                                      kw.get("extra_body")))
            result = self._chat(model_id, messages, client_name=client_name, **kw)
            if hosted_judge:
                spend.note_judge_call(result)
                bigger = judge_length_retry_tokens(result, kw.get("max_tokens"), self.judge_max_output_tokens)
                if bigger:
                    log.warning("judge call on %s cut at %s output tokens with no complete verdict: asked once "
                                "more with %d", model_id, kw.get("max_tokens"), bigger)
                    result = self._chat(model_id, messages, client_name=client_name, **{**kw, "max_tokens": bigger})
                    spend.note_judge_call(result)
            return result

    def _chat(self, model_id: str, messages: list[dict], *,
              client_name: str | None = None, **kw):
        """One streamed completion with retries (see api.stream_chat_retried).

        ``client_name`` overrides the identity a StudioForge caller declares
        itself under (default ``studioforge.LEASE_HOLDER`` = "crucibleforge")
        — the judge phase passes "crucibleforge-judge" so its X-SF-Client
        agrees with its own lease holder (rig-integration contract
        §12.1). Per §12.2, a StudioForge request also carries an explicit
        ``priority`` (default 2 — agent tier); the caller may
        override or, non-studioforge providers, it is simply omitted."""
        if self.type == "claude_code":
            # subscription CLI: sampling / budgets are not settable (see claudecode.py)
            from . import claudecode
            return claudecode.run_chat(model_id, messages, timeout=self.timeout,
                                       tools=kw.get("tools"), effort=kw.get("cc_effort"))
        from .api import stream_chat_retried
        body_extra = merge_extra_body(self.extra_body, kw.pop("extra_body", None))
        if self.reasoning_replay:
            # The chain must arrive in reasoning_content for the replay to
            # work, and MiniMax's API only does that when reasoning_split is
            # set (it normalises M3/M2.x's inline <think> into the channel the
            # tool path already speaks). It is REQUIRED to be true on
            # M3.1-Flash (the API 400s on false) and is accepted by all six
            # MiniMax models. Every request to this provider carries it.
            body_extra["reasoning_split"] = True
        kw.setdefault("timeout", self.timeout)
        if self.rename_params:
            kw.setdefault("rename_params", self.rename_params)
        if self.drop_params:
            kw.setdefault("drop_params", self.drop_params)
        headers = dict(self.headers)
        if self.type == "studioforge":
            headers.setdefault("X-SF-Client", client_name or studioforge.LEASE_HOLDER)
            kw.setdefault("priority", self.request_priority)
        if self.type == "anthropic":
            kw["wire"] = "anthropic"
        route = cache_route_for(self.base_url, self.cache_route)
        key = None
        if route:
            key = (spend.call_key() if cache_route_scope_for(self.base_url, self.cache_route_scope) == "call"
                   else spend.episode_key(model_id))
        if key:
            where, field_name = route
            if where == "header":
                headers.setdefault(field_name, key)
            else:
                body_extra.setdefault(field_name, key)
        price = self.prices.get(model_id)
        # the spend gate's type: "local" for an OpenAI-shaped server on a local address (is_hosted), never billed
        stype = self.type if self.is_hosted else "local"
        if spend.is_hosted(stype):
            spend.note_judge_call()
        if not price:
            # the hosted judge needs no price (owner 2026-11-24: the judge is not part of the model's cost; the run
            # summary then says "judge cost not priced"); the global ceiling still gates a priced judge
            if (spend.is_hosted(stype) and not ALLOW_UNPRICED and not spend.is_judge()
                    and spend.explicit_cap_set(model_id)):
                # fail closed: an unpriced hosted call cannot be bounded by a cap somebody set.
                # bench45's standing default cap alone does not arm this (fix round 2026-11-18,
                # P4); a real run of an unpriced hosted model is refused at cli.cmd_run instead
                raise spend.SpendCapExceeded(
                    f"crucibleforge spend gate: hosted model {model_id!r} (provider {self.name!r}) has "
                    "no price entry, so no spend cap can bound it - call not sent. Add "
                    "`price: {input: X, output: Y}` per million tokens to models.yaml, or pass "
                    "--allow-unpriced")
            # unpriced (local / rig) calls are never capped, counted or journalled
            guard = self._placement_guard(model_id)
            if guard is not None:
                kw["pre_send"] = guard
            return stream_chat_retried(self.base_url, self.api_key, model_id, messages,
                                       headers=headers, verify_model=self.verify_model,
                                       extra_body=body_extra, **kw)
        # the spend cap lives in the client: a hosted call over the cap is
        # never sent (SpendCapExceeded is a WindowClosed: no row, job pending)
        spend.check(model_id, self.name)
        est = spend.estimate_call_cost(price, messages, kw.get("max_tokens"))
        token = spend.reserve(stype, model_id, est)
        try:
            result = stream_chat_retried(
                self.base_url, self.api_key, model_id, messages,
                headers=headers, verify_model=self.verify_model, extra_body=body_extra,
                pre_send=lambda: spend.guard(stype, model_id, own=token),
                on_billed_error=lambda e: spend.record_error(stype, model_id, price, e, token),
                **kw)
            spend.record(stype, model_id, price, result, estimate=est)
            return result
        finally:
            spend.release(token)


def cache_route_for(base_url: str, configured: str | None) -> tuple[str, str] | None:
    """Where this provider wants its prompt-cache routing key: ("body", field)
    or ("header", name), or None. ``configured`` (provider ``cache_route``:
    ``body:<field>`` | ``header:<name>`` | ``off``) wins; else by host --
    OpenAI ``prompt_cache_key``, xAI ``x-grok-conv-id``, OpenRouter
    ``session_id`` (CACHING-PROVIDERS.md). Other hosts cache without a hint."""
    if configured:
        c = configured.strip()
        if c.lower() in ("off", "none", "false"):
            return None
        where, _, name = c.partition(":")
        if where in ("body", "header") and name:
            return where, name
        return None
    host = (urlparse(base_url).hostname or "").lower()
    if host == "api.openai.com":
        return "body", "prompt_cache_key"
    if host == "api.x.ai":
        return "header", "x-grok-conv-id"
    if host.endswith("openrouter.ai"):
        return "body", "session_id"
    return None


def cache_route_scope_for(base_url: str, configured: str | None) -> str:
    """How long one routing key lives: "episode" (every turn of a (model, case, repeat) shares it)
    or "call" (a fresh key per request). ``configured`` (provider ``cache_route_scope``) wins; else
    xAI is "call" and every other host "episode".

    Why xAI is "call" (measured on grok-4.7, 2026-11-22): when a request continues a conversation
    that the SAME backend just served, xAI puts that turn's hidden reasoning back into the prompt
    (prompt_tokens grow by the previous turn's reasoning_tokens; the harness never sends it, and
    Chat Completions returns none of it). A sticky ``x-grok-conv-id`` makes every turn of an
    episode land there, so every continuation is replayed. Replayed turns ran 3-10x longer, used
    up to 8x the reasoning, often ended with reasoning and no visible answer (finish=stop, empty
    content: 11 of 13 tool chains scored 0 on 2026-11-22) and are where the 503s and the
    keep-alive-only hangs clustered. The same request on a fresh key: no replay, the answer in
    20-30 s. A fresh key per call keeps the conversation the harness sends as the whole input,
    the same protocol every other provider gets (reasoning_replay false)."""
    if configured:
        c = configured.strip().lower()
        if c in ("episode", "call"):
            return c
    host = (urlparse(base_url).hostname or "").lower()
    return "call" if host == "api.x.ai" else "episode"


# ------------------------------------------------------------- registry

# Keyed by id(cfg). The cache also pins the cfg dict itself: CPython reuses
# a freed dict's id, so a cfg built and dropped in one caller could otherwise
# hand the NEXT caller a provider built from a different config (seen as an
# order-dependent test failure, 2026-09-08 — harmless in the CLI, where one
# cfg lives for the whole process, but a real aliasing bug).
_CACHE: dict[tuple[int, str], tuple[dict, Provider]] = {}


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


#: Requests in flight at once for a hosted (OpenAI-compatible) API when its
#: models.yaml entry names no ``concurrency`` (suite 3.9: was 4 everywhere;
#: STEP4's MiniMax T1 ran 45 row-minutes at 4 wide in 21.6 min, its long
#: poles 3-6 min). Override per provider: ``providers.<name>.concurrency``.
API_DEFAULT_CONCURRENCY = 8


#: provider-block keys ending ``_mcp`` that the loader understands; any other
#: ``*_mcp`` key is a typo or a stale setting and is refused, never ignored
KNOWN_MCP_KEYS = frozenset({"render_vacate_mcp"})


def get_provider(cfg: dict, name: str) -> Provider:
    key = (id(cfg), name)
    if key in _CACHE:
        return _CACHE[key][1]
    p = provider_of(cfg, name)
    for _k in p:
        if str(_k).endswith("_mcp") and _k not in KNOWN_MCP_KEYS:
            raise ConfigError(f"providers.{name}: unknown key {_k!r} "
                              f"(known: {', '.join(sorted(KNOWN_MCP_KEYS))})")
    ptype = p.get("type", "openai")
    # claude_code is not in HOSTED_TYPES: it keeps concurrency 1 (one subscription CLI at a time)
    conc_raw = p.get("concurrency", API_DEFAULT_CONCURRENCY if ptype in HOSTED_TYPES
                     else 1 if ptype != "studioforge" else "auto")
    conc_auto = str(conc_raw).lower() == "auto"
    prov = Provider(
        name=name, type=ptype, base_url=str(p.get("base_url") or "").rstrip("/"),
        api_key=_resolve_key(p),
        concurrency=1 if conc_auto else max(1, int(conc_raw)),
        concurrency_explicit=not conc_auto,
        recommended_load=bool(p.get("recommended_load", True)),
        verify_model=bool(p.get("verify_model", ptype not in HOSTED_TYPES)),
        headers={k: _expand_env(v) for k, v in (p.get("headers") or {}).items()},
        timeout=float(p.get("timeout", 900)),
        extra_body=dict(p.get("extra_body") or {}),
        rename_params={str(k): str(v) for k, v in (p.get("rename_params") or {}).items()},
        drop_params=[str(k) for k in (p.get("drop_params") or [])],
        reasoning_replay=reasoning_replay_default(p),
        judge_max_output_tokens=int(p["judge_max_output_tokens"]) if p.get("judge_max_output_tokens") else None,
        max_output_tokens=(dict(p["max_output_tokens"]) if isinstance(p.get("max_output_tokens"), dict)
                           else int(p["max_output_tokens"]) if p.get("max_output_tokens") else None),
        hosted_override=None if p.get("hosted") is None else bool(p["hosted"]),
        lease=bool(p.get("lease", False)) and ptype == "studioforge",
        lease_devices=list(p["lease_devices"]) if p.get("lease_devices") else None,
        lease_idle_ttl_s=float(p.get("lease_idle_ttl_s", studioforge.LEASE_IDLE_TTL_S) or 0)
        or None,
        wait_busy_s=float(p.get("wait_busy_s", studioforge.DEFAULT_WAIT_BUSY_S)),
        min_slots=int(p.get("min_slots", 4)),
        target_slots=int(p.get("target_slots", 8)),
        restore_residents=bool(p.get("restore_residents", True)),
        widen_devices=str(p.get("widen_devices", "thinking")).lower(),
        use_resident=bool(p.get("use_resident", True)),
        force_evict_policy=bool(p.get("force_evict", False)) and ptype == "studioforge",
        lease_devices_preferred=(list(p["lease_devices_preferred"])
                                 if p.get("lease_devices_preferred") else None),
        render_vacate_mcp=(str(p["render_vacate_mcp"]).strip() or None) if p.get("render_vacate_mcp") else None,
        cache_route=(str(p["cache_route"]) if p.get("cache_route") else None),
        cache_route_scope=(str(p["cache_route_scope"]) if p.get("cache_route_scope") else None),
        kv_cache_type=(str(p["kv_cache_type"]).strip().lower() or None) if p.get("kv_cache_type") else None,
        request_priority=int(p.get("request_priority", 2)),
        exact_on_lease=bool(p.get("exact_on_lease", False)) and ptype == "studioforge",
        busy_unload_after_s=(float(p["busy_unload_after_s"])
                             if p.get("busy_unload_after_s") not in (None, False, "") else None),
    )
    for m in list(cfg.get("models") or []) + list((cfg.get("judge") or {}).get("candidates") or []):
        if isinstance(m, dict) and m.get("provider") == name and m.get("price") and m.get("model_id"):
            prov.prices.setdefault(str(m["model_id"]), dict(m["price"]))
    # the hosted judge's own entry ``<provider>~judge`` (cli._guard_45_judge): its price is the judge's, set on the
    # config so every Provider built from any copy of it books the judge's calls at that price
    for mid, pr in (((cfg.get("_judge_prices") or {}).get(name)) or {}).items():
        prov.prices[str(mid)] = dict(pr)
    if ptype == "studioforge" and LEASE_MODE[0]:
        apply_lease_mode(prov, LEASE_MODE[0])
    _CACHE[key] = (cfg, prov)
    return prov


# ------------------------------------------------------------ lease modes
# MakoBench operator (2026-11-06, OPERATOR-PLAN-v2 §6.3): `bench-all
# --lease-mode night|day` overrides the models.yaml bench-first policy for
# EVERY studioforge provider this process builds (generation, escalation,
# ladder and the judge alike — the judge reads the same Provider fields).
# Unset (no flag) = the models.yaml policy, unchanged.
#
#                         night                       day
#   force=true            idle residents (policy)     never
#   busy_unload_after_s   None (never cut a reply)    None
#   render-lease vacate   yes (policy)                never
#   request priority      2                           3
#   devices               planner / preferred cards   one unprotected card, else refuse
LEASE_MODES = ("night", "day")
#: the active mode for this process (a list so tests can reset it in place)
LEASE_MODE: list[str | None] = [None]


def apply_lease_mode(prov: "Provider", mode: str) -> "Provider":
    """Set ``prov``'s lease policy fields for ``mode`` (see the table above)."""
    if mode not in LEASE_MODES:
        raise ValueError(f"unknown lease mode {mode!r} (night|day)")
    prov.busy_unload_after_s = None
    if mode == "day":
        prov.force_evict_policy = False
        prov.force_evict = False
        prov.render_vacate_mcp = None
        prov.lease_devices_preferred = None
        prov.request_priority = 3
        prov.day_single_card = True
    return prov


def set_lease_mode(mode: str | None) -> None:
    """Make ``mode`` (or None = the models.yaml policy) the process-wide lease
    mode; providers built before are dropped so the next get_provider applies it."""
    if mode is not None and mode not in LEASE_MODES:
        raise ValueError(f"unknown lease mode {mode!r} (night|day)")
    LEASE_MODE[0] = mode
    clear_cache()


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


#: a hosted judge call cut at its output ceiling (finish=length) with no complete JSON object in its answer is asked
#: once more with this many times the output budget, capped at JUDGE_LENGTH_RETRY_CAP (or the provider entry's
#: ``judge_max_output_tokens``); a second cut answer is left to the judge's own handling (unparsable, pending)
JUDGE_LENGTH_RETRY_FACTOR = 4
JUDGE_LENGTH_RETRY_CAP = 32768


def is_openrouter(base_url: str | None) -> bool:
    host = (urlparse(str(base_url or "")).hostname or "").lower()
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


#: OpenRouter's documented switch for a model's reasoning (the request's ``reasoning`` object); it ignores the
#: ``chat_template_kwargs.enable_thinking`` a local llama.cpp / StudioForge server honours (probe 2026-11-24:
#: Qwen 3.8 27B still reasoned with it and answered with none under this)
OPENROUTER_THINKING_OFF = {"reasoning": {"enabled": False}}


def judge_thinking_off(base_url: str | None, extra: dict | None) -> dict:
    """A hosted judge's request extras with thinking OFF said the way its provider understands: on OpenRouter, a
    call that asks ``chat_template_kwargs.enable_thinking: false`` (the judge's no-think retry) also carries
    ``reasoning: {enabled: false}``. A ``reasoning`` object already present is the user's choice and is kept."""
    out = dict(extra or {})
    if not is_openrouter(base_url) or "reasoning" in out:
        return out
    ctk = out.get("chat_template_kwargs")
    if isinstance(ctk, dict) and ctk.get("enable_thinking") is False:
        out.update(OPENROUTER_THINKING_OFF)
    return out


def has_json_object(text: str | None) -> bool:
    """The answer holds one complete JSON object (a verdict the judge can read) outside any thinking block."""
    from .api import strip_thinking_tags
    from .judge import _last_json_object
    return _last_json_object(strip_thinking_tags(text or "")) is not None


def judge_length_retry_tokens(result, max_tokens, cap: int | None = None) -> int | None:
    """The output budget of the one retry of a judge call cut at its ceiling with no complete JSON object, or None
    (no retry: the answer ended on its own, holds a verdict, or the budget cannot grow)."""
    if getattr(result, "finish_reason", None) != "length" or not max_tokens:
        return None
    if has_json_object(getattr(result, "response_text", "")):
        return None
    bigger = min(int(max_tokens) * JUDGE_LENGTH_RETRY_FACTOR, int(cap or JUDGE_LENGTH_RETRY_CAP))
    return bigger if bigger > int(max_tokens) else None


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


#: prompt-cache multipliers of the input price when a ``price`` block names no
#: ``cache_write`` / ``cache_read`` (Anthropic's 5-minute-TTL write premium
#: and the generic read rate, claude-api skill shared/prompt-caching.md)
CACHE_WRITE_FACTOR = 1.25
CACHE_READ_FACTOR = 0.1


def cache_prices(entry: dict, native: bool = True) -> tuple[float, float] | None:
    """(cache_write, cache_read) USD per 1M tokens for a priced entry."""
    p = price_of(entry)
    if not p:
        return None
    price = entry.get("price") or {}
    # Claude-style defaults (write 1.25x, read 0.1x) only for the native
    # Anthropic path; every other provider with no declared cache_read is
    # priced at list (an undiscounted, overstated estimate, never a guess).
    wf, rf = (CACHE_WRITE_FACTOR, CACHE_READ_FACTOR) if native else (1.0, 1.0)
    try:
        cw = float(price["cache_write"]) if price.get("cache_write") is not None \
            else p[0] * wf
        # ``cached_input`` (the OpenAI / OpenRouter name for it) is the same thing as ``cache_read``
        cr_raw = price.get("cache_read") if price.get("cache_read") is not None else price.get("cached_input")
        cr = float(cr_raw) if cr_raw is not None else p[0] * rf
    except (TypeError, ValueError):
        return p[0] * wf, p[0] * rf
    return cw, cr


def cost_usd(entry: dict, prompt_tokens: int | None, completion_tokens: int | None,
             cache_read_tokens: int | None = None,
             cache_write_tokens: int | None = None,
             native_cache: bool | None = None) -> float | None:
    """USD for a call / row. ``prompt_tokens`` is the TOTAL input; the cache
    split (when the provider reported one) is priced at the cache rates and
    only the remainder at the input rate."""
    p = price_of(entry)
    if not p:
        return None
    pt = prompt_tokens or 0
    cr = max(0, cache_read_tokens or 0)
    cw = max(0, cache_write_tokens or 0)
    if cr or cw:
        if native_cache is None:
            native_cache = cache_write_tokens is not None
        cwp, crp = cache_prices(entry, native_cache)
        uncached = max(0, pt - cr - cw)
        return (uncached * p[0] + cw * cwp + cr * crp
                + (completion_tokens or 0) * p[1]) / 1_000_000
    return (pt * p[0] + (completion_tokens or 0) * p[1]) / 1_000_000


def call_cost_usd(entry: dict, r) -> float | None:
    """Billed cost of one ChatResult (its billed-extra tokens included)."""
    if r.prompt_tokens is None and r.completion_tokens is None and not (
            r.billed_extra_prompt_tokens or r.billed_extra_completion_tokens):
        return None
    return cost_usd(entry,
                    (r.prompt_tokens or 0) + (r.billed_extra_prompt_tokens or 0),
                    (r.completion_tokens or 0) + (r.billed_extra_completion_tokens or 0),
                    (r.cache_read_tokens or 0) + (r.billed_extra_cache_read_tokens or 0),
                    (r.cache_write_tokens or 0) + (r.billed_extra_cache_write_tokens or 0),
                    native_cache=r.cache_write_tokens is not None)
