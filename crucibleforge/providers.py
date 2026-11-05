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

from . import lms, studioforge
from .config import ConfigError, provider_of

log = logging.getLogger(__name__)

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
    # the model fits there empty; ``clawforge_mcp`` lets a foreign render
    # lease on the wanted cards be vacated (and resumed afterwards).
    force_evict_policy: bool = False
    lease_devices_preferred: list | None = None
    clawforge_mcp: str | None = None
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
    _vacated_render: bool = field(default=False, repr=False)
    # remembers what /models returned (None = endpoint doesn't support listing)
    _models_cache: set | None = field(default=None, repr=False)
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

    # ---------------------------------------------------------- discovery
    def auth_wall(self, probe: bool = False) -> int | None:
        """401/403 when the server last answered /models with an auth refusal
        (reachable, but it wants a key / rejected ours), else None. ``probe``
        issues a fresh GET /models when nothing has been recorded yet."""
        if self.last_probe_status is None and probe:
            self.alive()
        return self.last_probe_status if self.last_probe_status in (401, 403) else None

    def alive(self) -> bool:
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
                return self.type == "openai" and resp.status_code in (401, 403, 404, 405)
        except Exception:
            return False

    def _headers(self, client_name: str | None = None) -> dict:
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
                lease = studioforge.acquire_lease(
                    self.base_url, self.api_key, hdrs, devices, model_ids=[model_id],
                    reason=f"crucibleforge benchmark: {model_id.rsplit('/', 1)[-1]}",
                    idle_ttl_s=self.lease_idle_ttl_s, wait_busy_s=self.wait_busy_s,
                    force=self.force_evict and not self.day_single_card,
                    busy_unload_after_s=(self.busy_unload_after_s
                                         if self.force_evict and not self.day_single_card
                                         else None))
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
            lease["_model_id"] = model_id
            self._lease = lease
            # a lease can evict residents (force_evict): they are restored
            # even when the phase fails before any load (review S5)
            self._touched = True
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
            # poll (every READY_POLL_S = 2 s): a lease that is loading the
            # model is waited for; one that did not start a load is detected
            # on the first poll instead of after a fixed 20 s
            studioforge.wait_ready(model_id, self.base_url, self.api_key, headers=hdrs,
                                   never_appeared_grace_s=studioforge.READY_POLL_S,
                                   timeout_s=600)
        except studioforge.StudioForgeError as e:
            log.info("lease did not produce a ready %s (%s) — loading it explicitly",
                     model_id.rsplit("/", 1)[-1], str(e)[:120])
        studioforge.load_model(model_id, self.base_url, self.api_key, context_length,
                               recommended=self.recommended_load, headers=hdrs,
                               wait_busy_s=self.wait_busy_s,
                               min_slots=self.min_slots, target_slots=self.target_slots)
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

        self._keepalive = threading.Thread(target=_loop, name="crucibleforge-lease-keepalive",
                                           daemon=True)
        self._keepalive.start()

    def claim_devices(self, model_id: str, exclude=()) -> list[int]:
        """The cards a lease for ``model_id`` should name, bench-first style:
        ``lease_devices_preferred`` when the planner says the model fits
        there empty, else ``lease_devices`` (or every card). Then, if a
        foreign RENDER lease stands on those cards, vacate it through
        ClawForge and remember to resume it on release. ``exclude``: cards
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
                vacatable_render=bool(self.clawforge_mcp))
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
                                            self.clawforge_mcp):
            self._vacated_render = True
        return devices

    def release_lease(self) -> None:
        """Release the standing GPU lease (idempotent; safe at exit), then
        hand the render tenant its card back if we vacated it."""
        lease = self._lease
        if lease is not None:
            self._lease = None
            studioforge.release_lease(self.base_url, self.api_key, self.mgmt_headers(),
                                      lease["_lease_id"])
        if self._vacated_render:
            self._vacated_render = False
            try:
                studioforge.clawforge_control(self.clawforge_mcp, "resume", timeout=60)
                log.info("ClawForge resumed (render lease handed back)")
            except studioforge.StudioForgeError as e:
                log.warning("ClawForge resume failed — image generation stays vacated "
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
            if self.day_single_card:
                # day mode: unload exactly the models this run loaded — never
                # a resident someone else loaded while we ran
                ours = [m for m in self._plans if m not in before]
                if ours:
                    studioforge.unload_models(self.base_url, self.api_key,
                                              self.mgmt_headers(), ours)
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

    # ------------------------------------------------------------ chat
    def chat(self, model_id: str, messages: list[dict], *,
             client_name: str | None = None, **kw):
        """One streamed completion with retries (see api.stream_chat_retried).

        ``client_name`` overrides the identity a StudioForge caller declares
        itself under (default ``studioforge.LEASE_HOLDER`` = "crucibleforge")
        — the judge phase passes "crucibleforge-judge" so its X-SF-Client
        agrees with its own lease holder (rig-integration contract
        §12.1). Per §12.2, a StudioForge request also carries an explicit
        ``priority`` (default 2 — agent tier); the caller may
        override or, non-studioforge providers, it is simply omitted."""
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
        headers = dict(self.headers)
        if self.type == "studioforge":
            headers.setdefault("X-SF-Client", client_name or studioforge.LEASE_HOLDER)
            kw.setdefault("priority", self.request_priority)
        return stream_chat_retried(self.base_url, self.api_key, model_id, messages,
                                   headers=headers, verify_model=self.verify_model,
                                   extra_body=body_extra, **kw)


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


def get_provider(cfg: dict, name: str) -> Provider:
    key = (id(cfg), name)
    if key in _CACHE:
        return _CACHE[key][1]
    p = provider_of(cfg, name)
    ptype = p.get("type", "openai")
    conc_raw = p.get("concurrency", API_DEFAULT_CONCURRENCY if ptype == "openai"
                     else 1 if ptype != "studioforge" else "auto")
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
        reasoning_replay=reasoning_replay_default(p),
        lease=bool(p.get("lease", False)) and ptype == "studioforge",
        lease_devices=list(p["lease_devices"]) if p.get("lease_devices") else None,
        lease_idle_ttl_s=float(p.get("lease_idle_ttl_s", studioforge.LEASE_IDLE_TTL_S) or 0)
        or None,
        wait_busy_s=float(p.get("wait_busy_s", studioforge.DEFAULT_WAIT_BUSY_S)),
        min_slots=int(p.get("min_slots", 4)),
        target_slots=int(p.get("target_slots", 8)),
        restore_residents=bool(p.get("restore_residents", True)),
        use_resident=bool(p.get("use_resident", True)),
        force_evict_policy=bool(p.get("force_evict", False)) and ptype == "studioforge",
        lease_devices_preferred=(list(p["lease_devices_preferred"])
                                 if p.get("lease_devices_preferred") else None),
        clawforge_mcp=(str(p["clawforge_mcp"]).strip() or None) if p.get("clawforge_mcp") else None,
        busy_unload_after_s=(float(p["busy_unload_after_s"])
                             if p.get("busy_unload_after_s") not in (None, False, "") else None),
    )
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
#   ClawForge vacate      yes (policy)                never
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
        prov.clawforge_mcp = None
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
