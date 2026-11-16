"""Model instances: N copies of ONE model, each on its own device set.

A rig serves a model under a registry id. To run the same weights twice at
once (two judge shards on disjoint GPU pairs, later a subject model in two
places), the second copy needs its own address. StudioForge may give it one
of two shapes, and this module keeps the rest of the code independent of
which:

* an ALIAS registry id (a StudioForge virtual model over the same GGUF, with
  its own ``device_override``): ``serve_id`` = the alias, ``instance`` = None;
* a NATIVE instance of the same id: ``serve_id`` = the model id and
  ``instance`` = the tag/port the server reports for that copy (requests may
  then need ``request_headers``).

What never changes is the IDENTITY: every row judged by any instance records
the canonical ``model_id`` as its judge (the board must not see a second
judge), and the instance that actually served it goes into a separate audit
field (:meth:`ModelInstance.audit`).

Integrity rule: two instances are interchangeable only when they serve the
same file at the same launch settings. :func:`parity_problems` compares the
server's own launch argv of two loaded instances, ignoring only the flags
that MUST differ (devices, port, alias, the split across cards, slot count
and the total context that follows from it, the host prompt-cache size).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelInstance:
    """Where one copy of ``model_id`` is served.

    ``serve_id``: the id loads, leases and requests use (default: model_id;
    StudioForge 2.2: a virtual model such as ``<id>@b`` over the same GGUF).
    ``instance``: a native instance tag/port, for a server that runs several
    copies under ONE id (StudioForge 2.2 does not; kept so the address stays
    pluggable). ``devices``: the whole cards this copy is leased on (None =
    the usual device choice). ``parallel``: slot counts to ask for, widest
    first; a refused load steps down (``(6, 5)``). ``name``: a short tag
    (``a``, ``b``) used in the lease holder (``crucibleforge-judge-b``) and
    the audit field. ``allow_devices``: cards a policy would normally never
    name (the judge's GPU 3 rule) that this copy may take because the
    operator said so. ``window``: ``"HH:MM-HH:MM"`` local time this copy may
    run in (None = any time). ``vacate``: register a vacate URL on the lease
    and give the cards back when a better class asks (StudioForge D56).
    ``priority``: the lease's class (3 = background)."""
    model_id: str
    serve_id: str | None = None
    instance: str | None = None
    devices: tuple[int, ...] | None = None
    parallel: tuple[int, ...] = ()
    name: str | None = None
    allow_devices: frozenset = field(default_factory=frozenset)
    window: str | None = None
    vacate: bool = False
    priority: int | None = None

    @property
    def sid(self) -> str:
        return self.serve_id or self.model_id

    @property
    def is_copy(self) -> bool:
        """Served under another address than the canonical one."""
        return self.sid != self.model_id or self.instance is not None

    def holder(self, base: str) -> str:
        """Lease holder for this instance: ``base`` or ``base-<name>``."""
        return f"{base}-{self.name}" if self.name else base

    def audit(self, **extra) -> dict:
        """The per-row audit record (never the identity)."""
        out = {"serve_id": self.sid}
        if self.instance is not None:
            out["instance"] = self.instance
        if self.name:
            out["name"] = self.name
        if self.devices is not None:
            out["devices"] = list(self.devices)
        out.update({k: v for k, v in extra.items() if v is not None})
        return out

    def matches_loaded(self, entry: dict) -> bool:
        """Whether a ``/api/status`` ``loaded[]`` entry is this instance."""
        if entry.get("model_id") != self.sid:
            return False
        if self.instance is None:
            return True
        tags = {str(entry.get(k)) for k in ("instance", "instance_id", "port") if entry.get(k) is not None}
        return str(self.instance) in tags


#: the keys a judge candidate dict carries for its instance (judge.instance_of)
CAND_KEYS = ("serve_model_id", "instance", "instance_name", "instance_devices",
             "instance_parallel", "instance_allow_devices", "instance_window",
             "instance_vacate", "instance_priority")


def to_cand(inst: ModelInstance) -> dict:
    """The candidate-dict keys for ``inst`` (merged into a judge candidate)."""
    out = {}
    if inst.serve_id and inst.serve_id != inst.model_id:
        out["serve_model_id"] = inst.serve_id
    if inst.instance is not None:
        out["instance"] = inst.instance
    if inst.name:
        out["instance_name"] = inst.name
    if inst.devices is not None:
        out["instance_devices"] = list(inst.devices)
    if inst.parallel:
        out["instance_parallel"] = list(inst.parallel)
    if inst.allow_devices:
        out["instance_allow_devices"] = sorted(inst.allow_devices)
    if inst.window:
        out["instance_window"] = inst.window
    if inst.vacate:
        out["instance_vacate"] = True
    if inst.priority is not None:
        out["instance_priority"] = int(inst.priority)
    return out


def from_cand(cand: dict) -> ModelInstance:
    """The instance a judge candidate dict describes (the canonical copy when
    it carries none of :data:`CAND_KEYS`)."""
    return from_args(cand["model_id"], serve_id=cand.get("serve_model_id"),
                     instance=cand.get("instance"), devices=cand.get("instance_devices"),
                     parallel=cand.get("instance_parallel"), name=cand.get("instance_name"),
                     allow_devices=cand.get("instance_allow_devices") or (),
                     window=cand.get("instance_window"), vacate=cand.get("instance_vacate"),
                     priority=cand.get("instance_priority"))


def parse_devices(text: str | None) -> tuple[int, ...] | None:
    """``"2,3"`` -> (2, 3); empty/None -> None. Raises ValueError on junk."""
    if text is None or str(text).strip() == "":
        return None
    out = tuple(int(x) for x in str(text).replace(" ", "").split(",") if x != "")
    if not out or len(set(out)) != len(out) or any(d < 0 for d in out):
        raise ValueError(f"bad device list {text!r} (want e.g. 2,3)")
    return out


def parse_parallel(v) -> tuple[int, ...]:
    """``6`` / ``"6,5"`` / ``[16, 14, 12]`` -> a widest-first ladder."""
    if v in (None, "", 0):
        return ()
    if isinstance(v, int):
        vals = [v]
    elif isinstance(v, str):
        vals = [int(x) for x in v.replace(" ", "").split(",") if x]
    else:
        vals = [int(x) for x in v]
    if any(n < 1 for n in vals):
        raise ValueError(f"bad slot ladder {v!r}")
    return tuple(sorted(set(vals), reverse=True))


def from_args(model_id: str, *, serve_id=None, instance=None, devices=None, parallel=None,
              name=None, allow_devices=(), window=None, vacate=False,
              priority=None) -> ModelInstance:
    if window:
        parse_window(window)                      # refuse junk early
    return ModelInstance(model_id=model_id, serve_id=serve_id or None,
                         instance=str(instance) if instance not in (None, "") else None,
                         devices=parse_devices(devices) if isinstance(devices, str) else
                         (tuple(int(d) for d in devices) if devices else None),
                         parallel=parse_parallel(parallel),
                         name=name or None,
                         allow_devices=frozenset(int(d) for d in allow_devices or ()),
                         window=window or None, vacate=bool(vacate),
                         priority=int(priority) if priority is not None else None)


# ------------------------------------------------------------------ config
# models.yaml (general: N instances of ANY model, for judging or generation):
#
#   instances:
#     <canonical model id>:
#       - {name: a, devices: [0, 1], parallel: [16, 14, 12]}
#       - {name: b, serve_id: "<canonical model id>@b", devices: [2, 3],
#          parallel: [6, 5], window: "22:00-08:00", vacate: true,
#          allow_devices: [3], priority: 3}
#   instance_vacate: {host: <this box's tailnet address>, port: 0}
#
# Instances are disjoint WHOLE-card sets the model fits on (StudioForge
# leases whole cards); each copy is its own registry id with its own VRAM
# accounting. The first entry is conventionally the canonical id.
def configured(cfg: dict, model_id: str) -> list[ModelInstance]:
    """Every instance models.yaml lists for ``model_id`` (may be empty)."""
    rows = ((cfg or {}).get("instances") or {}).get(model_id) or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError(f"instances.{model_id}: each entry must be a mapping")
        out.append(from_args(model_id, serve_id=r.get("serve_id") or r.get("id"),
                             instance=r.get("instance"), devices=r.get("devices"),
                             parallel=r.get("parallel"), name=r.get("name"),
                             allow_devices=r.get("allow_devices") or (),
                             window=r.get("window"), vacate=r.get("vacate", False),
                             priority=r.get("priority")))
    _check_disjoint(model_id, out)
    return out


def _check_disjoint(model_id: str, insts: list[ModelInstance]) -> None:
    seen: dict[int, str] = {}
    names = set()
    for i in insts:
        key = i.name or i.sid
        if key in names:
            raise ValueError(f"instances.{model_id}: duplicate instance {key!r}")
        names.add(key)
        for d in i.devices or ():
            if d in seen:
                raise ValueError(f"instances.{model_id}: GPU {d} is in both {seen[d]!r} and "
                                 f"{key!r} (instances need disjoint whole cards)")
            seen[d] = key


def find(cfg: dict, model_id: str, name: str) -> ModelInstance:
    for i in configured(cfg, model_id):
        if i.name == name or i.sid == name:
            return i
    raise ValueError(f"no instance {name!r} of {model_id} in models.yaml instances:")


# ------------------------------------------------------------------ window
def parse_window(spec: str) -> tuple[int, int]:
    """``"22:00-08:00"`` -> (start minute, end minute) of the day."""
    import re
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", str(spec or ""))
    if not m:
        raise ValueError(f"window {spec!r}: want HH:MM-HH:MM")
    a = int(m.group(1)) * 60 + int(m.group(2))
    b = int(m.group(3)) * 60 + int(m.group(4))
    if not (0 <= a < 1440 and 0 <= b < 1440) or a == b:
        raise ValueError(f"window {spec!r}: bad times")
    return a, b


def window_state(spec: str | None, now=None):
    """(inside, end) for a daily window: whether ``now`` (a naive local
    datetime) is inside it, and when the current/next stretch ends."""
    from datetime import datetime, timedelta
    now = now or datetime.now()
    if not spec:
        return True, None
    a, b = parse_window(spec)
    m = now.hour * 60 + now.minute
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    inside = (a <= m < b) if a < b else (m >= a or m < b)
    if a < b:
        end = day0 + timedelta(minutes=b)
    else:
        end = day0 + timedelta(minutes=b) + (timedelta(days=1) if m >= a else timedelta())
    return inside, (end if inside else None)


# ------------------------------------------------------------------ parity
#: launch flags that legitimately differ between two copies of one model
#: (value-taking flags; their value is dropped with them)
_PER_COPY_FLAGS = {"--alias", "--port", "--host", "--device", "--tensor-split", "--main-gpu",
                   "--parallel", "-np", "--ctx-size", "-c", "--cache-ram"}
#: plan keys that must be equal (per slot, not total)
_PLAN_KEYS = ("ctx_size", "kv_cache_type", "kv_cache_type_v", "flash_attn", "split_mode")
#: effective-settings keys that must be equal
_EFFECTIVE_KEYS = ("ctx_per_slot", "kv_unified", "batch_size", "ubatch_size", "spec_type",
                   "cache_prompt", "cache_reuse", "gpu_only", "n_gpu_layers", "slot_prompt_similarity",
                   "ctx_checkpoints")


def _argv_norm(argv: list) -> list[str]:
    out, skip = [], False
    for i, a in enumerate(argv or []):
        if i == 0:
            continue                      # the binary path/name
        if skip:
            skip = False
            continue
        a = str(a)
        if a in _PER_COPY_FLAGS:
            skip = True
            continue
        out.append(a)
    # flag order is the server's business; compare as flag -> value pairs
    pairs, i = [], 0
    while i < len(out):
        a = out[i]
        if a.startswith("-") and i + 1 < len(out) and not out[i + 1].startswith("--"):
            pairs.append(f"{a}={out[i + 1].replace(chr(92), '/').rsplit('/', 1)[-1]}")
            i += 2
        else:
            pairs.append(a)
            i += 1
    return sorted(pairs)


def parity_problems(ref: dict, copy: dict) -> list[str]:
    """Differences between two ``/api/status`` ``loaded[]`` entries that
    would make their verdicts not interchangeable (empty = same model, same
    launch). ``ref`` is the canonical instance, ``copy`` the other one."""
    probs = []
    if ref.get("state") != "ready" or copy.get("state") != "ready":
        probs.append(f"not both ready (ref {ref.get('state')}, copy {copy.get('state')})")
    ra, ca = _argv_norm(ref.get("launch_args")), _argv_norm(copy.get("launch_args"))
    if not ra or not ca:
        probs.append("launch argv not reported — parity cannot be proven")
    elif ra != ca:
        only_r = sorted(set(ra) - set(ca))
        only_c = sorted(set(ca) - set(ra))
        probs.append(f"launch argv differs: ref-only {only_r} copy-only {only_c}")
    rp, cp = ref.get("plan") or {}, copy.get("plan") or {}
    for k in _PLAN_KEYS:
        if rp.get(k) != cp.get(k):
            probs.append(f"plan.{k}: ref {rp.get(k)!r} copy {cp.get(k)!r}")
    re_, ce = ref.get("effective") or {}, copy.get("effective") or {}
    for k in _EFFECTIVE_KEYS:
        if re_.get(k) != ce.get(k):
            probs.append(f"effective.{k}: ref {re_.get(k)!r} copy {ce.get(k)!r}")
    if ref.get("resolved_engine_tag") != copy.get("resolved_engine_tag"):
        probs.append(f"engine: ref {ref.get('resolved_engine_tag')!r} "
                     f"copy {copy.get('resolved_engine_tag')!r}")
    return probs


def registry_problems(canonical: dict | None, copy: dict | None) -> list[str]:
    """Static check on two ``/v1/models`` records: the copy must be the same
    weights (an alias: a virtual model over the canonical base with no
    adapters and no preset; a native copy: the same record)."""
    if not canonical or not copy:
        return ["registry record missing for "
                + ("the canonical model" if not canonical else "the copy")]
    if canonical is copy or canonical.get("id") == copy.get("id"):
        return []
    probs = []
    sc, sx = canonical.get("studioforge") or {}, copy.get("studioforge") or {}
    if sx.get("is_virtual"):
        if sx.get("base_model_id") != canonical.get("id"):
            probs.append(f"alias base is {sx.get('base_model_id')!r}, not {canonical.get('id')!r}")
        if sx.get("adapters"):
            probs.append("alias carries adapters")
        if sx.get("preset"):
            probs.append("alias carries a preset (system prompt / sampler defaults)")
    for k in ("quantization", "arch"):
        if canonical.get(k) != copy.get(k):
            probs.append(f"{k}: {canonical.get(k)!r} vs {copy.get(k)!r}")
    if sc.get("size_bytes") != sx.get("size_bytes"):
        probs.append(f"size_bytes: {sc.get('size_bytes')} vs {sx.get('size_bytes')}")
    return probs
