"""The judge beside generation, when the placement leaves it room (MakoBench 4.5).

``subject_copies.judge_mode`` derives the schedule from the placement: with the
subject on the 5090 pair only, the judge (``subject_placement.judge``, e.g. the
canonical judge on the 3090 pair) runs WHILE the subject generates; with subject
copies on both pairs it waits for generation (the classic after-phase). No hand
flag: the placement decides.

The overlapping judge
* uses its own config copy (``dict(cfg)``), so its Provider object (and lease)
  never touches the subject's;
* leases exactly its cards (holder ``crucibleforge-judge-<name>``, class 3, never
  ``force``) and loads the judge EXPLICITLY there (load_exact: devices, KV, slot
  ladder at the judge context); a judge that lands anywhere else is unloaded and
  the overlap is off (the after-phase judges everything, as before);
* judges in passes (``run_judge`` on the run's labels: each pass takes the rows
  generated so far), then one last pass after generation, then unloads and
  releases. Rows record the canonical judge id; the copy is the audit field.
Judge time is inside the run's recorded wall-clock either way (meta ``judged``).
"""
from __future__ import annotations

import logging
import threading

from . import stopflag, studioforge
from . import subject_copies as sc

log = logging.getLogger(__name__)

#: seconds between judge passes while generation runs
PASS_GAP_S = 30.0


def mode_for_run(cfg: dict, labels: list[str]) -> tuple[str | None, dict | None]:
    """(mode, judge instance) for a run of ``labels``: "concurrent" only when every rig
    model in it leaves the judge's cards free; None without a declared placement."""
    from .providers import get_provider
    inst = sc.judge_instance(cfg)
    jdevs = (inst or {}).get("instance_devices")
    modes = set()
    for e in cfg.get("models") or []:
        if e.get("name") not in labels:
            continue
        try:
            if get_provider(cfg, e["provider"]).type != "studioforge":
                continue
        except Exception:  # noqa: BLE001 - an unknown provider places nothing
            continue
        modes.add(sc.judge_mode(sc.declared(cfg, e), jdevs))
    modes.discard(None)
    if not modes:
        return None, inst
    return ("concurrent" if modes == {"concurrent"} else "after"), inst


class OverlapJudge:
    """Judge passes on a background thread while generation runs."""

    def __init__(self, cfg: dict, labels: list[str], inst: dict, deadline: float | None = None,
                 stop: threading.Event | None = None):
        self.cfg = dict(cfg)                 # own provider cache = own Provider + lease
        self.labels = list(labels)
        self.inst = dict(inst)
        self.deadline = deadline
        self.stop = stop or threading.Event()
        self.gen_done = threading.Event()
        self.result = {"passes": 0, "judged": 0, "error": None, "loaded": False}
        self._t: threading.Thread | None = None
        self._provider = None
        self._rig = None
        self._copy: sc.SubjectCopy | None = None

    # ----------------------------------------------------------- lifecycle
    def start(self) -> "OverlapJudge":
        self._t = threading.Thread(target=self._run, name="crucibleforge-overlap-judge",
                                   daemon=True)
        self._t.start()
        return self

    def finish(self, timeout: float | None = None) -> dict:
        """Generation is over: one last pass, then unload and release."""
        self.gen_done.set()
        if self._t is not None:
            self._t.join(timeout)
        return self.result

    def _run(self) -> None:
        try:
            self._bring_up()
            self.result["loaded"] = True
            while not self._halted():
                self._pass()
                if self.gen_done.is_set():
                    break
                self.gen_done.wait(PASS_GAP_S)
            if not self._halted():
                self._pass()        # the rows the last generation minutes produced
        except Exception as e:  # noqa: BLE001 - the after-phase judges what is left
            log.error("overlapping judge stopped: %s — the after-phase judges the rest", e)
            self.result["error"] = str(e)
        finally:
            self._tear_down()

    def _self_grades(self) -> bool:
        """A 4.5 profile's judge grades its own rows (amendment r), as the after-phase judge does."""
        from . import judge45, profiles
        try:
            return judge45.self_grades(profiles.load_profile(self.cfg.get("_profile") or profiles.DEFAULT_PROFILE,
                                                             self.cfg))
        except Exception:  # noqa: BLE001 -- no readable profile: the refusal stands
            return False

    def _halted(self) -> bool:
        return self.stop.is_set() or stopflag.ABORT.is_set()

    def _pass(self) -> None:
        from .judge import run_judge
        # no parallel ladder in the instance: the copy is already loaded exactly as
        # declared, and JudgeClient.load must not reload it (it would drop the KV type)
        inst = {k: v for k, v in self.inst.items() if k != "instance_parallel"}
        r = run_judge(self.cfg, self.labels, retry_failed=False, stop=self.stop,
                      deadline=self.deadline, instance=inst, allow_self_judge=self._self_grades())
        self.result["passes"] += 1
        self.result["judged"] += int(r.get("judged") or 0)

    # ------------------------------------------------------------- the rig
    def _bring_up(self) -> None:
        from .judge import JUDGE_LEASE_HOLDER, select_judge
        from .providers import get_provider
        cand = select_judge(self.cfg, set(), instance=self.inst)
        prov = get_provider(self.cfg, cand["provider"])
        if prov.type != "studioforge":
            raise sc.CopyError(f"judge {cand['model_id']} is not on the rig")
        prov.own_unload_only = True
        # run_judge's load then finds it resident and ready (the fast path): it never
        # reaches the provider's load-recommended path
        prov.use_resident = True
        self._provider = prov
        ctx = int(cand.get("context_length") or cand.get("context") or 16384)
        devices = tuple(int(d) for d in self.inst.get("instance_devices") or ())
        if not devices:
            raise sc.CopyError("the judge placement names no devices")
        slots = tuple(self.inst.get("instance_parallel") or (4, 3, 2))
        c = sc.SubjectCopy(name=self.inst.get("instance_name") or "j", model_id=cand["model_id"],
                           serve_id=cand.get("serve_model_id") or cand["model_id"],
                           devices=devices, slots=tuple(sorted(slots, reverse=True)),
                           kv=sc.wanted_kv(self.cfg))
        rig = self._rig = sc.Rig(prov, holder_base=JUDGE_LEASE_HOLDER)
        # 02:10 incident rule: only a distinct virtual id that nobody else serves or names
        if c.serve_id == c.model_id:
            raise sc.CopyError(f"the overlapping judge needs its own virtual id (serve_id), not "
                               f"the base id {c.model_id}")
        if rig.status_entry(c.serve_id) is not None:
            raise sc.CopyError(f"judge {c.serve_id} is already loaded by someone else; refused")
        named = [l.get("holder") for l in studioforge.list_leases(rig.base, rig.key, rig.hdrs()) or []
                 if c.serve_id in (l.get("model_ids") or [])]
        if named:
            raise sc.CopyError(f"judge {c.serve_id} is named by lease(s) {named}; refused")
        before = rig.base_fingerprint(c.model_id)
        self._copy = c
        lid = rig.lease(c, rig.holder(c.name))
        prov._lease = {"_lease_id": lid, "_devices": list(devices), "_model_id": c.serve_id}
        prov._touched = True
        prov._start_keepalive()
        live = rig.load(c, ctx)
        after = rig.base_fingerprint(c.model_id)
        if after != before:
            rig.unload(c.serve_id, c.name)
            raise sc.BaseTouched(f"judge base {c.model_id} changed during the overlap judge's "
                                 f"bring-up ({before} -> {after}); refused")
        probs = sc._check_live(c, live or {}, ctx)
        if int((live or {}).get("parallel") or 0) < 2:
            probs.append("fewer than 2 slots (the resident fast path needs a multi-slot load)")
        if probs:
            rig.unload(c.serve_id, c.name)
            raise sc.Misplaced(f"judge {c.serve_id} is not as declared: " + "; ".join(probs))
        prov._remember_plan(c.serve_id, live=live)
        log.info("overlapping judge ready: %s, %s slots x ctx %s, KV %s on %s", c.serve_id,
                 live.get("parallel"), live.get("ctx_size"), live.get("kv_cache_type"),
                 live.get("devices"))

    def _tear_down(self) -> None:
        prov, c = self._provider, self._copy
        if prov is None:
            return
        try:
            if c is not None and self._rig is not None:
                self._rig.unload(c.serve_id, c.name)     # guarded: only our own instance
        except Exception as e:  # noqa: BLE001
            log.warning("overlapping judge unload failed: %s", e)
        try:
            prov.release_lease()
        except Exception as e:  # noqa: BLE001
            log.warning("overlapping judge lease release failed: %s", e)
