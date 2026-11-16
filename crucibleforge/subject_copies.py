"""Subject copies: one model under test served by SEVERAL copies at once.

MakoBench 4.5 (Jake, R6: "assume it uses the entire rig, and can also split the
model and run two copies (one on the 5090s and one on the 3090s)"). Admin R's
placement design: EXP-20261113-25/design.md; numbers: anchors/rig_throughput.yaml.

A rig model may declare where its copies go. The profile sets it for every
StudioForge model (``subject_placement``); a registry entry's own ``instances:``
wins::

    subject_placement:
      kv_cache_type: q8_0                  # else run_settings.kv_cache_type
      copies:
        - {name: a, devices: [0, 1], slots: [12, 10, 8, 6, 5]}
        - {name: b, devices: [2, 3], slots: [5, 4]}          # serve id <model>@mb-b
      judge: {name: b, serve_id: "<judge id>@b", devices: [2, 3], parallel: [4, 3, 2]}

* EVERY copy -- the first one too -- is served under its own pre-created StudioForge virtual
  model (``serve_id``, default ``<model>@mb-<name>``) whose ``device_override``
  is its cards (a launch override = its own llama-server instance). The BASE id
  is never leased, loaded, unloaded or chatted to while copies are placed
  (2026-11-14 02:10 incident: a lease naming the base id made StudioForge load
  it at catalog context on our cards, which replaced another run's instance of
  the same id -- the server keeps one instance per id). A ``serve_id`` equal to
  the base id is refused; the base id's serving instance is fingerprinted
  before and after every load and any change refuses the run. Aliases are
  admin-made and fixed (creating one is admin-only by design): an existing alias
  is used only when its ``device_override`` already equals the copy's cards
  (nothing is ever patched); a missing one refuses the run naming the alias an
  admin must create (:class:`AliasNeeded`). Aliases are never deleted. Two
  aliases on the same cards (``@p-a`` / ``@mb-a``) are one per card pair at a
  time: the lease and the exact load on those cards enforce it.
* Every copy is leased on exactly its cards (class 3, never ``force``) by a
  lease that names NO model (a named model is loaded by the lease itself at its
  catalog context) and loaded EXPLICITLY there (``load_exact`` with devices, KV type and the slot
  ladder; never load-recommended). Before any job the live devices must sit
  inside the lease and the KV type must be the run's: otherwise the copy is
  unloaded and the run refuses (2026-11-13 incident: a lease on [2,3] that did
  not load, then a load-recommended fallback put the model on [0,1]).
* Context per slot is the run's load context; a copy's ``ctx_per_slot`` may only
  RAISE it (R23: no token budget cuts an answer).
* Parity, before the first job: registry (alias over the canonical base, no
  preset, no adapters, same quant/arch/size) and launch argv
  (``instances.parity_problems``: same file, template, sampler flags, KV, ctx
  per slot, engine). Any difference refuses the run.
* One job pool with workers = total live slots; a job takes a slot on the copy
  with the most free slots (a per-copy capacity, so a slow copy only ever has
  its own slots busy) and keeps it for all its turns. Rows keep the canonical
  ``model_id``; the copy goes to ``row["subject_copy"]`` (audit only).
* A copy that fails to load is dropped before the run (the run goes on with the
  others, the meta records why); one that dies mid-run is marked dead, its
  in-flight job is re-run on a live copy (no row written for the lost attempt),
  and the run continues. No copy left -> :class:`NoCopyLeft` (the normal model
  error path).
* Teardown on every exit path: unload every copy, release every copy's lease,
  delete aliases created for the run -- only ids and leases this placement made.
* While copies are placed the run talks to them through :class:`PlacedProvider`:
  a chat addressed to the base id goes to the calling job's copy (or the first
  live copy for probes and the conformance gate), and any load/switch of the
  base id raises.

Judge scheduling is DERIVED from the placement (:func:`judge_mode`): when the
subject copies leave the judge's cards free, the judge runs beside generation
("concurrent"); when they take them, judging waits for generation ("after").
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import instances as _instances
from . import studioforge

log = logging.getLogger(__name__)

#: lease holder for subject copies: "<LEASE_HOLDER>-<name>"
LEASE_PRIORITY = 3
#: how often a copy's lease is touched (seconds)
TOUCH_S = 120.0


class CopyError(RuntimeError):
    """A copy could not be brought up as declared, or copies are not interchangeable."""


class NoCopyLeft(CopyError):
    """Every copy of the subject died."""


class Misplaced(CopyError):
    """A copy came up somewhere other than declared (cards outside its lease, another KV
    type, other slots): unloaded, and the run is refused (2026-11-13 incident class)."""


class NotMine(CopyError):
    """An instance we were about to unload or reload is not ours: another client loaded it
    or has requests in flight on it, or the server answered 409. Stop; never retry.
    (2026-11-14 02:10: an explicit unload of a shared id cut another run's live streams --
    the server allowed it because both runs authenticate with the same key.)"""


class AliasNeeded(CopyError):
    """The copy's virtual id does not exist and this key may not create it (401/403):
    an admin must create it. Never answered by falling back to the base id."""


class BaseTouched(CopyError):
    """The base id's serving instance changed while a copy came up (cards, pid, ctx):
    another holder's run may have been disturbed -- the run is refused."""


class SlotAborted(Exception):
    """The model's run was aborted while a job waited for a slot."""


@dataclass
class SubjectCopy:
    name: str
    model_id: str
    serve_id: str
    devices: tuple[int, ...] | None
    slots: tuple[int, ...]
    kv: str | None = None
    ctx_per_slot: int | None = None
    # runtime
    live: dict = field(default_factory=dict)
    lease_id: str | None = None
    virtual_created: bool = False
    alive: bool = False
    in_use: int = 0
    jobs: int = 0
    died: str | None = None

    @property
    def canonical(self) -> bool:
        """Never true since 2026-11-14: no copy is served under the base id."""
        return self.serve_id == self.model_id

    @property
    def capacity(self) -> int:
        return int(self.live.get("parallel") or 0) if self.alive else 0

    def audit(self) -> dict:
        """The per-row record of which copy served it (never the identity)."""
        out = {"name": self.name, "serve_id": self.serve_id,
               "devices": list(self.live.get("devices") or self.devices or []),
               "slots": self.live.get("parallel"), "kv_cache_type": self.live.get("kv_cache_type"),
               "ctx_per_slot": self.live.get("ctx_size")}
        return {k: v for k, v in out.items() if v not in (None, [])}

    def record(self) -> dict:
        """The meta record of this copy (placement, live plan, fate)."""
        return {**self.audit(), "declared_devices": list(self.devices or []),
                "declared_slots": list(self.slots), "alive": self.alive, "jobs": self.jobs,
                **({"died": self.died} if self.died else {})}


# ------------------------------------------------------------------- config
def copy_serve_id(model_id: str, name: str, row: dict | None = None) -> str:
    """How a copy is addressed on the rig -- the ONE place to change when StudioForge's
    lease-owned instances (``<model>:2``, created by ``POST /api/leases`` with an
    ``instances`` block; dev branch, not live) replace today's fixed admin-made aliases."""
    return str((row or {}).get("serve_id") or f"{model_id}@mb-{name}")


def _profile(cfg: dict) -> dict:
    try:
        from . import profiles
        name = (cfg or {}).get("_profile") or profiles.active_name(cfg)
        return profiles.load_profile(name, cfg) if name else {}
    except Exception as e:  # noqa: BLE001 - no profile = no placement
        log.debug("profile not read for subject placement (%s)", e)
        return {}


def placement_block(cfg: dict) -> dict:
    """The profile's ``subject_placement`` (or ``cfg['subject_placement']`` for a run
    without a profile file), {} when none."""
    blk = (cfg or {}).get("subject_placement")
    if blk is None:
        blk = _profile(cfg).get("subject_placement")
    return dict(blk or {})


def wanted_kv(cfg: dict, blk: dict | None = None) -> str | None:
    blk = placement_block(cfg) if blk is None else blk
    kv = blk.get("kv_cache_type") or ((_profile(cfg).get("run_settings") or {})
                                      .get("kv_cache_type"))
    return str(kv).strip().lower() or None if kv else None


def declared(cfg: dict, entry: dict) -> list[SubjectCopy]:
    """The copies declared for ``entry`` (its own ``instances:`` > the profile's
    ``subject_placement.copies``); [] = the classic single-copy path."""
    blk = placement_block(cfg)
    rows = entry.get("instances")
    if rows is None:
        # the profile's placement covers the models its ``match`` regex names (searched in
        # "<label> <model_id>"; none = every StudioForge model): a model the cards cannot
        # hold is left to the classic path instead of being refused
        pat = blk.get("match")
        import re
        if pat and not re.search(str(pat), f"{entry.get('name', '')} {entry.get('model_id', '')}"):
            return []
        rows = blk.get("copies")
    if not rows:
        return []
    model_id = entry["model_id"]
    kv = wanted_kv(cfg, blk)
    out: list[SubjectCopy] = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise CopyError(f"subject copies of {model_id}: each entry must be a mapping")
        name = str(r.get("name") or chr(ord("a") + i))
        sid = copy_serve_id(model_id, name, r)
        if sid == model_id:
            raise CopyError(f"subject copy {name} of {model_id}: a copy is never served under "
                            f"the base id (a load of it replaces whatever instance serves it)")
        slots = _instances.parse_parallel(r.get("slots") or r.get("parallel"))
        if not slots:
            raise CopyError(f"subject copy {name} of {model_id}: needs a slot ladder (slots: [..])")
        devs = r.get("devices")
        devices = (_instances.parse_devices(devs) if isinstance(devs, str)
                   else tuple(int(d) for d in devs) if devs else None)
        if devices is None:
            raise CopyError(f"subject copy {name} of {model_id}: needs its devices")
        out.append(SubjectCopy(name=name, model_id=model_id, serve_id=str(sid), devices=devices,
                               slots=slots,
                               kv=(str(r["kv_cache_type"]).lower() if r.get("kv_cache_type") else kv),
                               ctx_per_slot=int(r["ctx_per_slot"]) if r.get("ctx_per_slot") else None))
    seen: dict[int, str] = {}
    for c in out:
        for d in c.devices or ():
            if d in seen:
                raise CopyError(f"subject copies of {model_id}: GPU {d} is in both {seen[d]!r} "
                                f"and {c.name!r} (copies need disjoint whole cards)")
            seen[d] = c.name
    if len({c.name for c in out}) != len(out):
        raise CopyError(f"subject copies of {model_id}: duplicate copy names")
    return out


def judge_instance(cfg: dict) -> dict | None:
    """The judge copy the placement names (``subject_placement.judge``), as judge
    candidate keys (instances.to_cand), or None."""
    j = placement_block(cfg).get("judge")
    if not j:
        return None
    from . import profiles
    jid = j.get("model_id")
    if not jid:
        pj = profiles.profile_judge(_profile(cfg))
        jid = pj.get("model_id") if isinstance(pj, dict) else pj
    if not jid:
        return None
    inst = _instances.from_args(jid, serve_id=j.get("serve_id"), devices=j.get("devices"),
                                parallel=j.get("parallel"), name=j.get("name"),
                                allow_devices=j.get("allow_devices") or j.get("devices") or (),
                                priority=j.get("priority", 3))
    return _instances.to_cand(inst)


def judge_mode(copies: list[SubjectCopy] | list[dict], judge_devices) -> str | None:
    """"concurrent" when the subject copies leave the judge's cards free (the judge
    runs beside generation), "after" when they take any of them (judging waits for
    generation, then may use every card), None without a declared placement."""
    if not copies:
        return None
    if not judge_devices:
        return "after"
    used: set[int] = set()
    for c in copies:
        devs = c.devices if isinstance(c, SubjectCopy) else c.get("devices")
        used.update(int(d) for d in devs or ())
    return "after" if used & {int(d) for d in judge_devices} else "concurrent"


def plan_for_cfg(cfg: dict, entry: dict | None = None) -> dict:
    """{"copies": [...], "judge": cand-or-None, "judge_mode": ...} for a model (or the
    profile's default placement when ``entry`` is None)."""
    copies = declared(cfg, entry) if entry else declared(cfg, {"model_id": "?", "name": "?"})
    jc = judge_instance(cfg)
    return {"copies": copies, "judge": jc,
            "judge_mode": judge_mode(copies, (jc or {}).get("instance_devices"))}


# ---------------------------------------------------------------- the pool
_tls = threading.local()


def current() -> SubjectCopy | None:
    """The copy the calling worker thread holds a slot on (None outside a job)."""
    return getattr(_tls, "copy", None)


class CopyPool:
    """Slot accounting over the live copies (one condition, per-copy capacity)."""

    def __init__(self, copies: list[SubjectCopy]):
        self.copies = copies
        self._cv = threading.Condition()

    def live(self) -> list[SubjectCopy]:
        return [c for c in self.copies if c.alive]

    def total_slots(self) -> int:
        return sum(c.capacity for c in self.live())

    def _pick(self, exclude: set) -> SubjectCopy | None:
        best = None
        for c in self.copies:
            if not c.alive or c.name in exclude or c.in_use >= c.capacity:
                continue
            free = c.capacity - c.in_use
            if best is None or free > best.capacity - best.in_use:
                best = c
        return best

    @contextmanager
    def slot(self, exclude: set | None = None, abort=None):
        exclude = set(exclude or ())
        with self._cv:
            while True:
                if not [c for c in self.live() if c.name not in exclude]:
                    if not self.live():
                        raise NoCopyLeft("every copy of the subject is gone: "
                                         + "; ".join(f"{c.name}: {c.died}" for c in self.copies))
                    exclude = set()       # only excluded copies are left: use them
                    continue
                c = self._pick(exclude)
                if c is not None:
                    break
                if abort is not None and abort.is_set():
                    raise SlotAborted()
                self._cv.wait(1.0)
            c.in_use += 1
            c.jobs += 1
        prev = current()
        _tls.copy = c
        try:
            yield c
        finally:
            _tls.copy = prev
            with self._cv:
                c.in_use -= 1
                self._cv.notify_all()

    def mark_dead(self, c: SubjectCopy, why: str) -> None:
        with self._cv:
            if c.alive:
                log.error("subject copy %s (%s on %s) is gone: %s — the run goes on with %s",
                          c.name, c.serve_id, list(c.devices or []), why,
                          [x.name for x in self.copies if x.alive and x is not c] or "nothing")
            c.alive = False
            c.died = c.died or why
            self._cv.notify_all()


# ------------------------------------------------------------ rig handling
class Rig:
    """The StudioForge calls the copies need (one object, so tests swap it)."""

    def __init__(self, provider, holder_base: str | None = None):
        self.p = provider
        self.base, self.key = provider.base_url, provider.api_key
        self.holder_base = holder_base or studioforge.LEASE_HOLDER

    def holder(self, name: str) -> str:
        """The lease holder and X-SF-Client label of one copy."""
        return f"{self.holder_base}-{name}"

    def ours(self, client, name: str) -> bool:
        """A client label this process uses for copy ``name`` (its loads, its chats)."""
        return str(client or "") in (self.holder_base, studioforge.LEASE_HOLDER, self.holder(name))

    def hdrs(self, holder: str | None = None) -> dict:
        return self.p.mgmt_headers(client_name=holder)

    def models(self) -> dict:
        return {m.get("id"): m for m in studioforge.list_models_full(self.base, self.key) or []}

    def settings(self, sid: str) -> dict | None:
        code, data = studioforge._mgmt("GET", self.base, self.key, self.hdrs(),
                                       f"/api/models/{studioforge._quote(sid)}/settings")
        return data if code < 400 and isinstance(data, dict) else None

    def create_virtual(self, sid: str, base_id: str, devices) -> None:
        for method, path, body in (
                ("POST", "/api/virtual-models", {"id": sid, "base_model_id": base_id}),
                ("PATCH", f"/api/models/{studioforge._quote(sid)}/settings",
                 {"device_override": list(devices), "priority": LEASE_PRIORITY})):
            code, data = studioforge._mgmt(method, self.base, self.key, self.hdrs(), path, json=body)
            if code in (401, 403) and method == "POST":
                raise AliasNeeded(
                    f"alias needed: this key may not create virtual models (HTTP {code}). An admin "
                    f"must create {sid!r} as a bare alias of {base_id!r} with device_override "
                    f"{list(devices)} and priority {LEASE_PRIORITY}; the run never falls back to "
                    f"the base id")
            if code >= 400:
                raise CopyError(f"{method} {path}: HTTP {code} {str(data)[:200]}")

    def delete_virtual(self, sid: str) -> None:
        code, data = studioforge._mgmt("DELETE", self.base, self.key, self.hdrs(),
                                       f"/api/virtual-models/{studioforge._quote(sid)}")
        if code >= 400 and code != 404:
            log.warning("delete of the run's virtual model %s: HTTP %d %s", sid, code, str(data)[:160])

    def lease(self, c: SubjectCopy, holder: str) -> str:
        # a crashed run's lease of this copy: our exact holder, the same cards, naming nothing
        try:
            for old in studioforge.list_leases(self.base, self.key, self.hdrs(holder)) or []:
                if (str(old.get("holder") or "") == holder and not old.get("model_ids")
                        and sorted(old.get("devices") or []) == sorted(c.devices)):
                    log.warning("stale lease %s of copy %s (holder %s) — releasing it first",
                                old.get("id"), c.name, holder)
                    studioforge.release_lease(self.base, self.key, self.hdrs(), str(old.get("id")))
        except studioforge.StudioForgeError as e:
            log.debug("lease list unreadable (%s): no stale cleanup", e)
        # model_ids=[]: a lease that names a model loads it at catalog context (02:10 incident)
        lease = studioforge.acquire_lease(
            self.base, self.key, self.hdrs(holder), list(c.devices), model_ids=[],
            holder=holder, reason=f"crucibleforge benchmark: {c.model_id.rsplit('/', 1)[-1]} "
                                  f"(copy {c.name})",
            idle_ttl_s=studioforge.LEASE_IDLE_TTL_S, wait_busy_s=self.p.wait_busy_s,
            force=False, priority=LEASE_PRIORITY)
        return lease["_lease_id"]

    def touch(self, lease_id: str) -> None:
        studioforge.touch_lease(self.base, self.key, self.hdrs(), lease_id)

    def release(self, lease_id: str) -> None:
        studioforge.release_lease(self.base, self.key, self.hdrs(), lease_id)

    def guard_mine(self, sid: str, name: str, what: str) -> dict | None:
        """The live entry of ``sid`` if it is ours to touch (None: not loaded); NotMine when
        another client loaded it or has requests in flight on it. The server cannot be the
        guard here: every run of ours uses the same key, and the key proves holdership."""
        e = self.status_entry(sid)
        if e is None:
            return None
        by = e.get("loaded_by_client")
        foreign = sorted({str(f.get("client")) for f in e.get("in_flight") or []
                          if not self.ours(f.get("client"), name)})
        if (by and not self.ours(by, name)) or foreign:
            raise NotMine(f"refusing to {what} {sid}: loaded by {by!r}, requests in flight from "
                          f"{foreign or 'nobody else'} -- not ours")
        return e

    def load(self, c: SubjectCopy, ctx: int) -> dict:
        # load_exact unloads a resident whose shape differs before loading: only ever let it
        # do that to an instance this process owns (02:10 incident)
        self.guard_mine(c.serve_id, c.name, "reload")
        # every load carries ctx_size + parallel (load_exact's body), never the catalog default
        return studioforge.load_exact(c.serve_id, self.base, self.key, self.hdrs(self.holder(c.name)),
                                      int(ctx), c.slots, devices=list(c.devices), kv=c.kv)

    def unload(self, sid: str, name: str) -> None:
        if self.guard_mine(sid, name, "unload") is None:
            return                                    # not loaded: nothing to do
        # no force anywhere (the server decides force from the caller); X-SF-Client = holder
        code, data = studioforge._mgmt("POST", self.base, self.key, self.hdrs(self.holder(name)),
                                       f"/api/models/{studioforge._quote(sid)}/unload", json={})
        if code == 409:
            raise NotMine(f"unload of {sid}: 409 {str(data)[:160]} -- not ours; stopping, no retry")
        if code >= 400 and code != 404:
            raise CopyError(f"unload of {sid}: HTTP {code} {str(data)[:160]}")

    def status_entry(self, sid: str) -> dict | None:
        for m in studioforge.status(self.base, self.key, self.hdrs()).get("loaded", []):
            if m.get("model_id") == sid:
                return m
        return None

    def plan(self, sid: str) -> dict | None:
        return studioforge.loaded_plan(sid, self.base, self.key, self.hdrs())

    def base_fingerprint(self, model_id: str) -> dict:
        """The base id's serving instance (cards, pid, port, ctx, slots) and the leases
        naming it -- what a copy's bring-up must leave exactly as it found it."""
        inst = None
        for m in studioforge.status(self.base, self.key, self.hdrs()).get("loaded", []):
            if m.get("model_id") == model_id:
                plan = m.get("plan") or {}
                inst = [sorted(plan.get("devices") or m.get("devices") or []), m.get("pid"),
                        m.get("port"), plan.get("ctx_size"), plan.get("parallel")]
        named = sorted([str(l.get("holder")), sorted(l.get("devices") or [])]
                       for l in studioforge.list_leases(self.base, self.key, self.hdrs()) or []
                       if model_id in (l.get("model_ids") or []))
        return {"instance": inst, "leases_naming_base": named}


def _check_live(c: SubjectCopy, live: dict, ctx: int) -> list[str]:
    """What is wrong with a copy's live plan (empty = as declared)."""
    probs = []
    if (live or {}).get("state") != "ready":
        probs.append(f"state {(live or {}).get('state')!r}")
    devs = {int(d) for d in (live or {}).get("devices") or []}
    if not devs:
        probs.append("live devices not reported")
    elif not devs <= set(c.devices or ()):
        probs.append(f"live devices {sorted(devs)} are outside its lease {list(c.devices)}")
    if c.kv and str((live or {}).get("kv_cache_type") or "").lower() != c.kv:
        probs.append(f"KV {(live or {}).get('kv_cache_type')!r}, the run setting is {c.kv!r}")
    if int((live or {}).get("ctx_size") or 0) < int(ctx):
        probs.append(f"ctx per slot {(live or {}).get('ctx_size')} < {ctx}")
    if int((live or {}).get("parallel") or 0) not in set(c.slots):
        probs.append(f"{(live or {}).get('parallel')} slots, not one of {list(c.slots)}")
    return probs


#: placements with something on the rig (torn down by cli.release_all_leases on a hard exit)
ACTIVE: "set[Placement]" = set()
_ACTIVE_LOCK = threading.Lock()


def tear_down_all() -> int:
    """Tear down every placement still up (the stop watchdog / hard-exit path)."""
    with _ACTIVE_LOCK:
        live = list(ACTIVE)
    for pl in live:
        try:
            pl.tear_down()
        except Exception as e:  # noqa: BLE001 - best effort on the way out
            log.warning("placement tear-down failed: %s", e)
    return len(live)


class Placement:
    """The copies of one subject for one load round: bring up, verify, serve, tear down."""

    def __init__(self, provider, copies: list[SubjectCopy], ctx: int, rig: Rig | None = None):
        self.provider = provider
        self.copies = copies
        self.ctx = max([int(ctx or 0)] + [int(c.ctx_per_slot or 0) for c in copies])
        self.rig = rig or Rig(provider)
        self.pool = CopyPool(copies)
        self._saved: dict = {}
        self._stop = threading.Event()
        self._keep: threading.Thread | None = None
        self.load_s = 0.0
        self.notes: list[str] = []

    # ---------------------------------------------------------- bring up
    def bring_up(self) -> None:
        """Lease + exact load of every copy; drop the ones that fail; parity check.
        Raises CopyError (after tearing down) when nothing came up or parity fails."""
        t0 = time.perf_counter()
        with _ACTIVE_LOCK:
            ACTIVE.add(self)
        try:
            for c in self.copies:
                try:
                    self._bring_up_one(c)
                except Exception as e:  # noqa: BLE001 - an extra copy that cannot load is dropped
                    if isinstance(e, (Misplaced, BaseTouched, AliasNeeded, NotMine)) or (
                            isinstance(e, CopyError) and "alias" in str(e)):
                        # a copy off its cards, a disturbed base instance, or an alias with
                        # other settings (never patched): each refuses the run
                        raise CopyError(f"copy {c.name} ({c.serve_id}): {e}") from e
                    c.alive, c.died = False, f"did not come up: {e}"
                    self.notes.append(f"copy {c.name} dropped: {e}")
                    log.error("subject copy %s (%s) did not come up: %s", c.name, c.serve_id, e)
                    self._teardown_one(c)
            if not self.pool.live():
                raise NoCopyLeft("no copy of the subject came up: "
                                 + "; ".join(f"{c.name}: {c.died}" for c in self.copies))
            self._parity()
        except BaseException:
            self.tear_down()
            raise
        self.load_s = time.perf_counter() - t0
        self._keep = threading.Thread(target=self._keepalive, daemon=True,
                                      name="crucibleforge-copy-keepalive")
        self._keep.start()

    def _bring_up_one(self, c: SubjectCopy) -> None:
        if c.serve_id == c.model_id:
            raise CopyError(f"copy {c.name}: refusing to lease or load the base id {c.model_id}")
        recs = self.rig.models()
        if c.serve_id in recs:
            st = self.rig.settings(c.serve_id) or {}
            have = [int(d) for d in st.get("device_override") or []]
            if sorted(have) != sorted(c.devices):
                raise CopyError(f"copy {c.name}: the existing alias {c.serve_id} has "
                                f"device_override {have}, not {list(c.devices)} — its "
                                f"settings are not changed by a run; refused")
        else:
            # aliases are admin-made and fixed (rig 2026-11-14 06:55): reused, never created
            raise AliasNeeded(
                f"copy {c.name}: alias {c.serve_id!r} does not exist. An admin must create it as a "
                f"bare alias of {c.model_id!r} with device_override {list(c.devices)} and priority "
                f"{LEASE_PRIORITY}; the run never creates aliases and never falls back to the base id")
        before = self.rig.base_fingerprint(c.model_id)
        log.info("base %s before copy %s: %s", c.model_id, c.name, before)
        c.lease_id = self.rig.lease(c, self.rig.holder(c.name))
        live = self.rig.load(c, self.ctx)
        after = self.rig.base_fingerprint(c.model_id)
        if after != before:
            self.rig.unload(c.serve_id, c.name)
            raise BaseTouched(f"copy {c.name}: the base instance of {c.model_id} changed during "
                              f"its bring-up ({before} -> {after}); refused")
        # the early return of load_exact may hand back a resident that is not as declared
        probs = _check_live(c, live or {}, self.ctx)
        if probs:
            self.rig.unload(c.serve_id, c.name)
            raise Misplaced(f"copy {c.name} ({c.serve_id}) is not as declared: "
                            + "; ".join(probs))
        c.live = dict(live)
        c.alive = True
        log.info("subject copy %s ready: %s, %s slots x ctx %s, KV %s, devices %s", c.name,
                 c.serve_id, live.get("parallel"), live.get("ctx_size"),
                 live.get("kv_cache_type"), live.get("devices"))

    def _parity(self) -> None:
        live = self.pool.live()
        recs = self.rig.models()
        # registry: every copy is a bare alias over the base (no preset/adapters) -- one copy too
        for c in live:
            probs = _instances.registry_problems(recs.get(c.model_id), recs.get(c.serve_id))
            if probs:
                raise CopyError(f"copy {c.name} ({c.serve_id}) is not a bare alias of "
                                f"{c.model_id}: " + "; ".join(probs))
        if len(live) < 2:
            return
        ref_c = live[0]                      # launch parity against the first copy that came up
        ref = self.rig.status_entry(ref_c.serve_id) or {}
        for c in live:
            if c is ref_c:
                continue
            probs = _instances.parity_problems(ref, self.rig.status_entry(c.serve_id) or {})
            if probs:
                raise CopyError(f"copy {c.name} ({c.serve_id}) is not the same model as "
                                f"{ref_c.serve_id}: " + "; ".join(probs))
        log.info("subject copies %s: same file, template, sampler flags, KV and ctx per slot "
                 "(launch parity proven)", [c.name for c in live])

    def _keepalive(self) -> None:
        while not self._stop.wait(TOUCH_S):
            for c in self.copies:
                if c.alive and c.lease_id:
                    try:
                        self.rig.touch(c.lease_id)
                    except Exception as e:  # noqa: BLE001
                        log.warning("touch of copy %s lease failed: %s", c.name, e)

    # -------------------------------------------------------------- health
    def healthy(self, c: SubjectCopy) -> bool:
        """The copy is still loaded, ready, on its cards at the same plan."""
        try:
            live = self.rig.plan(c.serve_id)
        except studioforge.StudioForgeError:
            return True                   # unreadable status is not evidence of death
        if not live:
            return False
        if live.get("state") != "ready":
            return False
        return all(live.get(k) == c.live.get(k) for k in ("parallel", "ctx_size", "devices")
                   if c.live.get(k) is not None)

    def reload(self, c: SubjectCopy) -> bool:
        """Bring a lost copy back on its own lease, explicitly as declared (the last copy
        standing only; with others alive a lost copy is dropped instead). False = gone."""
        try:
            live = self.rig.load(c, self.ctx)
            probs = _check_live(c, live or {}, self.ctx)
            if probs:
                self.rig.unload(c.serve_id, c.name)
                raise CopyError("; ".join(probs))
        except Exception as e:  # noqa: BLE001
            self.pool.mark_dead(c, f"reload failed: {e}")
            return False
        c.live = dict(live)
        log.warning("subject copy %s reloaded as declared (%s slots on %s)", c.name,
                    live.get("parallel"), live.get("devices"))
        return True

    def record(self) -> dict:
        return {"copies": [c.record() for c in self.copies],
                "total_slots": self.pool.total_slots(), "ctx_per_slot": self.ctx,
                **({"notes": list(self.notes)} if self.notes else {})}

    # ------------------------------------------------------------ tear down
    def _teardown_one(self, c: SubjectCopy) -> None:
        if c.serve_id == c.model_id:
            return                        # never: the base id is not ours to unload
        for what, fn in (("unload", lambda: self.rig.unload(c.serve_id, c.name)),
                         ("lease release", lambda: c.lease_id and self.rig.release(c.lease_id)),
                         ("virtual delete",
                          lambda: c.virtual_created and self.rig.delete_virtual(c.serve_id))):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - every step is tried
                log.warning("copy %s %s failed: %s", c.name, what, e)
        c.lease_id = None
        c.virtual_created = False

    def tear_down(self) -> None:
        self._stop.set()
        with _ACTIVE_LOCK:
            ACTIVE.discard(self)
        for c in self.copies:
            self._teardown_one(c)
            c.alive = False
        for k, v in self._saved.items():
            setattr(self.provider, k, v)
        self._saved = {}

    # ------------------------------------------------------- the run's provider
    def first_live(self) -> SubjectCopy | None:
        live = self.pool.live()
        return live[0] if live else None


class PlacedProvider:
    """The run's provider while copies are placed: the base id is never loaded or
    switched, and a chat to it goes to a copy (the job's own, else the first live
    one -- the thinking probe and the conformance gate). Everything else delegates."""

    _REFUSED = ("switch_model", "_acquire", "claim_devices", "load_recommended", "ensure_loaded")

    def __init__(self, provider, placement: "Placement"):
        object.__setattr__(self, "_p", provider)
        object.__setattr__(self, "_pl", placement)

    def __getattr__(self, name):
        if name in PlacedProvider._REFUSED:
            def _refused(*a, **kw):
                raise CopyError(f"{name} refused: copies of the subject are placed, the base id "
                                f"is never loaded or switched (02:10 incident rule)")
            return _refused
        return getattr(self._p, name)

    def __setattr__(self, name, value):
        setattr(self._p, name, value)

    def __copy__(self):
        # conformance c3 copies the provider and flips a knob on the copy: copy the
        # inner provider (never mutate the bench's own) and keep the routing
        import copy as _copy
        return PlacedProvider(_copy.copy(self._p), self._pl)

    def _sid(self, model_id: str) -> str:
        held = current() or self._pl.first_live()
        if held is None:
            raise NoCopyLeft("no live copy of the subject to send to")
        return held.serve_id if model_id == held.model_id else model_id

    def chat(self, model_id, messages, **kw):
        return self._p.chat(self._sid(model_id), messages, **kw)

    def is_loaded(self, model_id) -> bool:
        if model_id == self._pl.copies[0].model_id:
            return bool(self._pl.pool.live())
        return self._p.is_loaded(model_id)

    def live_context(self, model_id):
        if model_id == self._pl.copies[0].model_id:
            c = current() or self._pl.first_live()
            return int((c.live if c else {}).get("ctx_size") or 0) or None
        return self._p.live_context(model_id)

    def loaded_plan_for(self, model_id):
        if model_id == self._pl.copies[0].model_id:
            c = self._pl.first_live()
            return dict(c.live) if c else {}
        return self._p.loaded_plan_for(model_id)
