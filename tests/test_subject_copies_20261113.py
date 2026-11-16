"""Subject copies (MakoBench 4.5, R6): one run on two served copies of the model under test.

Stubbed rig only (subject_copies.Rig is swapped for a fake). Every copy -- the first
too -- is its own virtual id; the base id is never leased, loaded or chatted to
(2026-11-14 02:10 incident). Covers: two copies at different speeds share
one pool with per-copy slot limits; rows keep the canonical id; a copy dying mid-run
hands its job to the other copy; mismatched or misplaced copies refuse the run before
any job; leases, loads and run-made aliases are released on every exit path; the judge
schedule is derived from the placement.
"""
from __future__ import annotations

import threading
import time

import pytest

from crucibleforge import config, providers, runner, stopflag, studioforge
from crucibleforge import subject_copies as sc
from crucibleforge.api import ChatResult, TransportError

MID = "org/Ref-27B-GGUF/Ref-27B-Q8_0"
SID_A = MID + "@mb-a"
SID_B = MID + "@mb-b"
ARGV = ["llama-server", "-m", "/models/Ref-27B-Q8_0.gguf", "--temp", "0.6",
        "--cache-type-k", "q8_0", "--jinja"]


@pytest.fixture(autouse=True)
def _flags():
    stopflag.ABORT.clear()
    runner.STOP.clear()
    providers.clear_cache()
    yield
    providers.clear_cache()


_cfg_last: list = [None]


def _cfg(copies=None, judge=None):
    placement = {"kv_cache_type": "q8_0",
                 "copies": copies if copies is not None else [
                     {"name": "a", "devices": [0, 1], "slots": [4, 3]},
                     {"name": "b", "devices": [2, 3], "slots": [2]}]}
    if judge:
        placement["judge"] = judge
    cfg = {"defaults": {"context_length": 8192, "repeats": {"instruct": 1}},
            "providers": {"p": {"type": "studioforge", "base_url": "http://p.invalid/v1",
                                "force_evict": True}},
            "judge": {"candidates": []}, "models": [], "subject_placement": placement}
    _cfg_last[0] = cfg
    return cfg


ENTRY = {"name": "ref", "model_id": MID, "provider": "p", "thinking": False}


def _case(cid):
    return {"id": cid, "category": "instruct", "prompt": f"say ok {cid}", "max_tokens": 64,
            "grader": "contains", "grader_config": {"needles": ["ok"]}}


class FakeRig:
    """The rig as subject_copies sees it. ``state`` is shared with the test."""
    state: dict = {}

    def __init__(self, provider):
        self.p = provider
        self.base, self.key = "http://p.invalid", ""
        self.s = FakeRig.state

    def hdrs(self, holder=None):
        return {}

    def models(self):
        base = {"id": MID, "quantization": "Q8_0", "arch": "qwen3",
                "studioforge": {"size_bytes": 29_000_000_000}}
        out = {MID: base}
        for sid in self.s["virtuals"]:
            out[sid] = {"id": sid, "quantization": "Q8_0", "arch": "qwen3",
                        "studioforge": {"size_bytes": 29_000_000_000, "is_virtual": True,
                                        "base_model_id": MID,
                                        **(self.s.get("alias_extra", {}) if sid == SID_B else {})}}
        return out

    def settings(self, sid):
        return self.s["virtuals"].get(sid)

    def create_virtual(self, sid, base_id, devices):
        self.s["virtuals"][sid] = {"device_override": list(devices)}
        self.s["log"].append(("create_virtual", sid))

    def delete_virtual(self, sid):
        self.s["virtuals"].pop(sid, None)
        self.s["log"].append(("delete_virtual", sid))

    def lease(self, c, holder):
        self.s["leases"][holder] = list(c.devices)
        self.s["log"].append(("lease", holder, list(c.devices)))
        return holder

    def touch(self, lid):
        pass

    def release(self, lid):
        self.s["leases"].pop(lid, None)
        self.s["log"].append(("release", lid))

    def load(self, c, ctx):
        fail = self.s.get("load_fail", {}).get(c.serve_id)
        if fail:
            raise studioforge.StudioForgeError(fail, status=507)
        devs = self.s.get("land_on", {}).get(c.serve_id, list(c.devices))
        plan = {"state": "ready", "parallel": c.slots[0], "ctx_size": ctx, "devices": devs,
                "kv_cache_type": c.kv}
        self.s["loaded"][c.serve_id] = plan
        self.s["log"].append(("load", c.serve_id, devs, c.slots[0], c.kv))
        return dict(plan)

    def holder(self, name):
        return f"crucibleforge-{name}"

    def unload(self, sid, name=None):
        self.s["loaded"].pop(sid, None)
        self.s["log"].append(("unload", sid))

    def status_entry(self, sid):
        plan = self.s["loaded"].get(sid)
        if plan is None:
            return None
        argv = list(self.s.get("argv", {}).get(sid, ARGV)) + ["--port", str(hash(sid) % 9000),
                                                                "--parallel", str(plan["parallel"])]
        return {"model_id": sid, "state": "ready", "launch_args": argv,
                "plan": {k: plan[k] for k in ("ctx_size", "kv_cache_type")},
                "effective": {"ctx_per_slot": plan["ctx_size"]},
                "resolved_engine_tag": "b12110"}

    def plan(self, sid):
        return self.s["loaded"].get(sid)

    def base_fingerprint(self, mid):
        assert mid == MID
        self.s["log"].append(("base_fingerprint",))
        return {"instance": self.s.get("base_instance", [[2, 3], 8524, 18101, 73728, 3]),
                "leases_naming_base": [["mblab-rigtput-d", [2, 3]]]}


def _rig(monkeypatch, speeds=None, die_after=None):
    """Patch the rig and the provider; returns the shared state."""
    st = {"virtuals": {SID_A: {"device_override": [0, 1]}, SID_B: {"device_override": [2, 3]}},
          "leases": {}, "loaded": {}, "log": [], "calls": {},
          "inflight": {}, "max_inflight": {}, "lock": threading.Lock(), "provider_release": 0}
    FakeRig.state = st
    monkeypatch.setattr(sc, "Rig", FakeRig)
    monkeypatch.setattr(sc, "TOUCH_S", 3600.0)
    monkeypatch.setattr(studioforge, "release_stale_leases", lambda *a, **k: 0)
    speeds = speeds or {SID_A: 0.02, SID_B: 0.06}

    def acquire(self, model_id, devices):
        pytest.fail(f"the provider's own lease/load path must not run for placed copies ({model_id})")

    def release_lease(self):
        if self._lease is not None:
            st["provider_release"] += 1
            st["leases"].pop("crucibleforge", None)
        self._lease = None

    def chat(self, model_id, messages, **kw):
        with st["lock"]:
            n = st["calls"][model_id] = st["calls"].get(model_id, 0) + 1
            st["inflight"][model_id] = st["inflight"].get(model_id, 0) + 1
            st["max_inflight"][model_id] = max(st["max_inflight"].get(model_id, 0),
                                               st["inflight"][model_id])
        try:
            assert model_id != MID, "the base id must never be chatted to while copies are placed"
            if model_id not in st["loaded"]:
                raise TransportError(f"{model_id}: connection refused")
            if die_after and model_id == die_after[0] and n > die_after[1]:
                st["loaded"].pop(model_id, None)        # the copy's server died
                raise TransportError(f"{model_id}: stream reset")
            time.sleep(speeds.get(model_id, 0.02))
            return ChatResult(response_text="ok", finish_reason="stop", served_model=model_id,
                              completion_tokens=5, reasoning_tokens=2, total_s=0.05)
        finally:
            with st["lock"]:
                st["inflight"][model_id] -= 1

    P = providers.Provider
    monkeypatch.setattr(P, "alive", lambda self: True)
    monkeypatch.setattr(P, "is_available", lambda self, mid: True)
    monkeypatch.setattr(P, "trained_context", lambda self, mid: None)
    monkeypatch.setattr(P, "model_capabilities", lambda self, mid: set())
    monkeypatch.setattr(P, "_acquire", acquire)
    monkeypatch.setattr(P, "release_lease", release_lease)
    monkeypatch.setattr(P, "chat", chat)
    monkeypatch.setattr(P, "switch_model",
                        lambda *a, **k: pytest.fail("declared copies must never go through "
                                                    "switch_model / load-recommended"))
    monkeypatch.setattr(P, "workers", lambda self, mid: pytest.fail("workers come from the copies"))
    return st


def _rows():
    return config.load_transcripts("ref")


def _assert_clean(st):
    """Nothing of any copy is left on the rig; no alias was created or deleted; the base id
    was never loaded or unloaded."""
    assert not [e for e in st["log"] if e[0] in ("create_virtual", "delete_virtual")]
    for sid, holder in ((SID_A, "crucibleforge-a"), (SID_B, "crucibleforge-b")):
        assert sid not in st["loaded"]
        assert holder not in st["leases"]
        assert sid in st["virtuals"]                 # pre-created aliases are never deleted
    assert not [e for e in st["log"] if e[0] in ("load", "unload") and e[1] == MID]


# ------------------------------------------------------------------ config
def test_declared_copies_parse_and_default_the_alias_id():
    copies = sc.declared(_cfg(), ENTRY)
    assert [c.name for c in copies] == ["a", "b"]
    assert copies[0].serve_id == SID_A and not copies[0].canonical
    assert copies[1].serve_id == SID_B and not copies[1].canonical
    assert copies[0].slots == (4, 3) and copies[1].devices == (2, 3)
    assert {c.kv for c in copies} == {"q8_0"}


def test_no_placement_is_the_classic_path():
    cfg = _cfg()
    cfg["subject_placement"] = {}
    assert sc.declared(cfg, ENTRY) == []


def test_overlapping_cards_are_refused():
    with pytest.raises(sc.CopyError, match="GPU 1"):
        sc.declared(_cfg([{"name": "a", "devices": [0, 1], "slots": [4]},
                          {"name": "b", "devices": [1, 2], "slots": [2]}]), ENTRY)


def test_a_registry_entry_instances_list_wins_over_the_profile():
    entry = {**ENTRY, "instances": [{"name": "solo", "devices": [0, 1], "slots": [10, 8]}]}
    copies = sc.declared(_cfg(), entry)
    assert [c.name for c in copies] == ["solo"] and copies[0].slots == (10, 8)


def test_judge_schedule_is_derived_from_the_placement():
    one = [{"devices": [0, 1]}]
    two = [{"devices": [0, 1]}, {"devices": [2, 3]}]
    assert sc.judge_mode(one, [2, 3]) == "concurrent"
    assert sc.judge_mode(two, [2, 3]) == "after"
    assert sc.judge_mode(one, None) == "after"
    assert sc.judge_mode([], [2, 3]) is None


# ------------------------------------------------------------- the run
def test_two_copies_at_different_speeds_share_one_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch)
    cfg = _cfg()
    summary = runner.run_models(cfg, [ENTRY], [_case(f"I{i}") for i in range(24)])
    assert not summary["ref"].get("failed"), summary
    rows = _rows()
    assert len(rows) == 24
    assert {r["model_id"] for r in rows} == {MID}                       # canonical identity
    served = {r["subject_copy"]["name"] for r in rows}
    assert served == {"a", "b"}
    for r in rows:
        assert r["subject_copy"]["kv_cache_type"] == "q8_0"
        assert r["metrics"]["answer_tokens"] == 3                       # 5 - 2 thinking
    # per-copy slot limits held; together they ran more than either alone
    assert st["max_inflight"][SID_A] <= 4 and st["max_inflight"][SID_B] <= 2
    assert st["max_inflight"][SID_A] + st["max_inflight"][SID_B] > 4
    # the faster copy took more of the work; the base id got nothing
    assert st["calls"][SID_A] > st["calls"][SID_B]
    assert MID not in st["calls"]
    # both loaded EXPLICITLY on their own cards with the run's KV type
    loads = [e for e in st["log"] if e[0] == "load"]
    assert ("load", SID_A, [0, 1], 4, "q8_0") in loads
    assert ("load", SID_B, [2, 3], 2, "q8_0") in loads
    meta = runner._read_meta("ref")
    pl = meta["plan"]["placement"]
    assert pl["total_slots"] == 6 and [c["name"] for c in pl["copies"]] == ["a", "b"]
    assert pl["judge_mode"] == "after"
    _assert_clean(st)
    assert not st["leases"] and st["provider_release"] == 0      # no provider lease at all


def test_a_copy_dying_mid_run_hands_its_job_over(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch, die_after=(SID_B, 2))
    summary = runner.run_models(_cfg(), [ENTRY], [_case(f"I{i}") for i in range(16)])
    assert not summary["ref"].get("failed"), summary
    rows = _rows()
    assert len(rows) == 16
    assert all(r.get("grade") == "pass" for r in rows), [r.get("grade") for r in rows]
    assert not [r for r in rows if r.get("error_kind")]                 # no lost attempt written
    assert sum(1 for r in rows if r["subject_copy"]["name"] == "b") <= 2
    meta = runner._read_meta("ref")
    b = [c for c in meta["plan"]["placement"]["copies"] if c["name"] == "b"][0]
    assert b["alive"] is False and "not loaded" in b["died"]
    _assert_clean(st)


def test_mismatched_copies_are_refused_before_any_job(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch)
    st["argv"] = {SID_B: ARGV[:-1] + ["--chat-template-file", "/t/other.jinja"]}
    summary = runner.run_models(_cfg(), [ENTRY], [_case("I1"), _case("I2")])
    assert summary["ref"].get("failed")
    assert "launch argv differs" in (summary["ref"].get("error") or "")
    assert not st["calls"]                                              # nothing was sent
    _assert_clean(st)


def test_an_alias_carrying_a_preset_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch)
    st["alias_extra"] = {"preset": {"system": "be nice"}}
    summary = runner.run_models(_cfg(), [ENTRY], [_case("I1")])
    assert summary["ref"].get("failed") and "preset" in summary["ref"]["error"]
    assert not st["calls"]
    _assert_clean(st)


def test_a_copy_that_lands_outside_its_lease_is_unloaded_and_the_run_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch)
    st["land_on"] = {SID_B: [0, 1]}              # the 2026-11-13 incident: wrong cards
    summary = runner.run_models(_cfg(), [ENTRY], [_case("I1")])
    assert summary["ref"].get("failed") and "outside its lease" in summary["ref"]["error"]
    assert ("unload", SID_B) in st["log"]
    assert not st["calls"]
    _assert_clean(st)


def test_wrong_kv_type_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch)
    orig = FakeRig.load

    def f16(self, c, ctx):
        live = orig(self, c, ctx)
        live["kv_cache_type"] = "f16"
        return live
    monkeypatch.setattr(FakeRig, "load", f16)
    summary = runner.run_models(_cfg(), [ENTRY], [_case("I1")])
    assert summary["ref"].get("failed") and "KV" in summary["ref"]["error"]
    _assert_clean(st)


def test_an_extra_copy_that_cannot_load_is_dropped_and_the_run_goes_on(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch)
    st["load_fail"] = {SID_B: "507 insufficient VRAM"}
    summary = runner.run_models(_cfg(), [ENTRY], [_case(f"I{i}") for i in range(6)])
    assert not summary["ref"].get("failed"), summary
    assert {r["subject_copy"]["name"] for r in _rows()} == {"a"}
    meta = runner._read_meta("ref")
    b = [c for c in meta["plan"]["placement"]["copies"] if c["name"] == "b"][0]
    assert b["alive"] is False and "507" in b["died"]
    _assert_clean(st)


def test_the_first_copy_failing_leaves_the_run_on_the_other(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch)
    st["load_fail"] = {SID_A: "507 insufficient VRAM"}
    summary = runner.run_models(_cfg(), [ENTRY], [_case(f"I{i}") for i in range(3)])
    assert not summary["ref"].get("failed"), summary
    assert {r["subject_copy"]["name"] for r in _rows()} == {"b"}
    _assert_clean(st)


# ------------------------------------------- 2026-11-14 02:10 incident rules
def test_a_copy_declared_under_the_base_id_is_refused():
    with pytest.raises(sc.CopyError, match="base id"):
        sc.declared(_cfg([{"name": "a", "serve_id": MID, "devices": [0, 1], "slots": [4]}]), ENTRY)


def test_a_disturbed_base_instance_refuses_the_run_and_unloads_the_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch)
    orig = FakeRig.load

    def load_moves_base(self, c, ctx):
        live = orig(self, c, ctx)
        st["base_instance"] = [[0, 1], 9999, 18102, 262144, 1]   # the base got replaced
        return live
    monkeypatch.setattr(FakeRig, "load", load_moves_base)
    summary = runner.run_models(_cfg(), [ENTRY], [_case("I1")])
    assert summary["ref"].get("failed") and "base instance" in summary["ref"]["error"]
    assert ("unload", SID_A) in st["log"]
    assert not st["calls"]
    _assert_clean(st)


def test_the_real_rig_lease_names_no_model(monkeypatch):
    sent = {}

    def acquire(base, key, hdrs, devices, model_ids=None, **kw):
        sent.update(devices=devices, model_ids=model_ids, force=kw.get("force"))
        return {"_lease_id": "L1"}
    stale = [{"id": "OLD", "holder": "crucibleforge-a", "devices": [0, 1], "model_ids": []},
             {"id": "D", "holder": "mblab-rigtput-d", "devices": [2, 3], "model_ids": [MID]},
             {"id": "MINE-OTHER", "holder": "crucibleforge-a", "devices": [2, 3], "model_ids": []}]
    released = []
    monkeypatch.setattr(studioforge, "acquire_lease", acquire)
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: stale)
    monkeypatch.setattr(studioforge, "release_lease", lambda b, k, h, lid: released.append(lid))
    prov = type("P", (), {"base_url": "x", "api_key": "", "wait_busy_s": 0,
                          "mgmt_headers": lambda self, client_name=None: {}})()
    c = sc.declared(_cfg(), ENTRY)[0]
    assert sc.Rig(prov).lease(c, "crucibleforge-a") == "L1"
    assert sent == {"devices": [0, 1], "model_ids": [], "force": False}
    assert released == ["OLD"]                     # only our own holder on the same cards


def test_placed_provider_routes_the_base_id_to_a_copy_and_refuses_loads():
    copies = sc.declared(_cfg(), ENTRY)
    for c, par in zip(copies, (4, 2)):
        c.alive, c.live = True, {"parallel": par, "ctx_size": 8192, "devices": list(c.devices)}
    pl = sc.Placement(type("P", (), {"base_url": "x", "api_key": ""})(), copies, 8192, rig=object())
    seen = []

    class Raw:
        name, type = "p", "studioforge"

        def chat(self, mid, messages, **kw):
            seen.append(mid)
            return "r"

        def switch_model(self, *a):
            pytest.fail("reached the raw provider")
    pp = sc.PlacedProvider(Raw(), pl)
    assert pp.chat(MID, []) == "r" and seen == [SID_A]            # probe / gate: first live copy
    with pl.pool.slot(exclude={"a"}):
        pp.chat(MID, [])
    assert seen[-1] == SID_B                                      # a job: its own copy
    with pytest.raises(sc.CopyError, match="never loaded"):
        pp.switch_model(MID, 8192)
    assert pp.live_context(MID) == 8192 and pp.is_loaded(MID) and pp.type == "studioforge"
    import copy
    raw = pp._p
    raw.reasoning_replay = False
    flipped = copy.copy(pp)
    flipped.reasoning_replay = True                               # conformance c3's flip
    assert raw.reasoning_replay is False and flipped._p is not raw
    flipped.chat(MID, [])
    assert seen[-1] == SID_A


def test_tear_down_all_reaches_every_active_placement():
    done = []

    class Pl:
        def tear_down(self):
            done.append(self)
            sc.ACTIVE.discard(self)
    a = Pl()
    sc.ACTIVE.add(a)
    assert sc.tear_down_all() == 1 and done == [a] and not sc.ACTIVE


def test_every_copy_dying_is_the_normal_model_error_and_leases_are_released(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch)
    orig_load = FakeRig.load
    calls = {"n": 0}

    def chat_kill(self, model_id, messages, **kw):
        calls["n"] += 1
        st["loaded"].clear()                      # the whole rig went away
        raise TransportError("connection refused")
    monkeypatch.setattr(providers.Provider, "chat", chat_kill)

    def no_reload(self, c, ctx):
        if any(e[0] == "load" and e[1] == c.serve_id for e in st["log"]):
            raise studioforge.StudioForgeError("rig unreachable", status=503)
        return orig_load(self, c, ctx)
    monkeypatch.setattr(FakeRig, "load", no_reload)
    summary = runner.run_models(_cfg(), [ENTRY], [_case(f"I{i}") for i in range(4)])
    assert summary["ref"].get("failed")
    _assert_clean(st)


def test_context_per_slot_is_never_below_the_run_context():
    copies = sc.declared(_cfg([{"name": "a", "devices": [0, 1], "slots": [4],
                                "ctx_per_slot": 4096}]), ENTRY)
    pl = sc.Placement(type("P", (), {"base_url": "x", "api_key": ""})(), copies, 73728,
                      rig=object())
    assert pl.ctx == 73728


def test_the_profile_placement_covers_only_the_models_it_matches():
    cfg = _cfg()
    cfg["subject_placement"]["match"] = r"Ref-27B"
    assert len(sc.declared(cfg, ENTRY)) == 2
    big = {"name": "big", "model_id": "org/Huge-122B-Q4", "provider": "p"}
    assert sc.declared(cfg, big) == []                                  # classic path


def test_a_missing_alias_the_key_may_not_create_refuses_the_run_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch)

    st["virtuals"].pop(SID_A)
    summary = runner.run_models(_cfg(), [ENTRY], [_case("I1")])
    err = summary["ref"].get("error") or ""
    assert summary["ref"].get("failed") and SID_A in err and "device_override [0, 1]" in err
    assert not st["calls"] and not [e for e in st["log"]
                                    if e[0] in ("lease", "load", "create_virtual")]


def test_the_real_rig_names_the_alias_on_403(monkeypatch):
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: (403, {"error": "needs admin"}))
    prov = type("P", (), {"base_url": "x", "api_key": "",
                          "mgmt_headers": lambda self, client_name=None: {}})()
    with pytest.raises(sc.AliasNeeded, match=r"@mb-a.*device_override \[0, 1\]"):
        sc.Rig(prov).create_virtual(SID_A, MID, (0, 1))


def test_an_alias_on_other_cards_is_refused_and_never_patched(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    st = _rig(monkeypatch)
    st["virtuals"][SID_B] = {"device_override": [0, 1]}
    summary = runner.run_models(_cfg(), [ENTRY], [_case("I1")])
    assert summary["ref"].get("failed") and "not changed by a run" in summary["ref"]["error"]
    assert st["virtuals"][SID_B] == {"device_override": [0, 1]}
    assert not st["calls"]



# ------------------------------- rig rules (StudioForge 2.3.0, lead 2026-11-14 07:00)
class _SF:
    """The real Rig over a recorded fake StudioForge management API."""

    def __init__(self, monkeypatch, status=None, unload_code=200):
        self.calls, self.status = [], status or []
        sf = self

        def mgmt(method, base, key, headers, path, json=None, timeout=None, **kw):
            sf.calls.append((method, path, dict(headers or {}), json))
            if path.endswith("/unload"):
                return unload_code, ({"error": "lease_conflict"} if unload_code == 409 else {})
            if path.endswith("/load"):
                return 200, {}
            return 200, {}
        monkeypatch.setattr(studioforge, "_mgmt", mgmt)
        monkeypatch.setattr(studioforge, "status", lambda *a, **k: {"loaded": sf.status})
        monkeypatch.setattr(studioforge, "loaded_plan", lambda *a, **k: {})
        monkeypatch.setattr(studioforge, "wait_ready", lambda *a, **k: None)
        prov = type("P", (), {"base_url": "x", "api_key": "", "wait_busy_s": 0,
                              "mgmt_headers": lambda self, client_name=None:
                              {"X-SF-Client": client_name or studioforge.LEASE_HOLDER}})()
        self.rig = sc.Rig(prov)

    def sent(self, suffix):
        return [c for c in self.calls if c[1].endswith(suffix)]


def _copy_a():
    return sc.declared(_cfg(), ENTRY)[0]


def test_rule1_no_force_on_lease_load_or_unload(monkeypatch):
    sf = _SF(monkeypatch, status=[{"model_id": SID_A, "loaded_by_client": "crucibleforge-a",
                                   "in_flight": []}])
    c = _copy_a()
    sf.status = []
    sf.rig.load(c, 73728)
    sf.status = [{"model_id": SID_A, "loaded_by_client": "crucibleforge-a", "in_flight": []}]
    sf.rig.unload(SID_A, "a")
    for method, path, hdrs, body in sf.calls:
        assert "force" not in (body or {}), (path, body)
    sent = {}
    monkeypatch.setattr(studioforge, "acquire_lease",
                        lambda *a, **k: sent.update(k) or {"_lease_id": "L"})
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: [])
    sf.rig.lease(c, "crucibleforge-a")
    assert sent["force"] is False


def test_rule2_holder_label_on_every_load_and_unload(monkeypatch):
    sf = _SF(monkeypatch)
    c = _copy_a()
    sf.rig.load(c, 73728)
    sf.status = [{"model_id": SID_A, "loaded_by_client": "crucibleforge-a", "in_flight": []}]
    sf.rig.unload(SID_A, "a")
    for _, path, hdrs, _ in sf.sent("/load") + sf.sent("/unload"):
        assert hdrs.get("X-SF-Client") == "crucibleforge-a", (path, hdrs)


def test_rule2_an_unload_answered_409_is_not_ours_and_never_retried(monkeypatch):
    sf = _SF(monkeypatch, status=[{"model_id": SID_A, "loaded_by_client": "crucibleforge-a",
                                   "in_flight": []}], unload_code=409)
    with pytest.raises(sc.NotMine, match="no retry"):
        sf.rig.unload(SID_A, "a")
    assert len(sf.sent("/unload")) == 1


@pytest.mark.parametrize("entry", [
    {"loaded_by_client": "mblab-rigtput-d", "in_flight": []},
    {"loaded_by_client": "crucibleforge-a", "in_flight": [{"client": "mblab-rigtput-d"}]},
])
def test_an_instance_another_client_loaded_or_uses_is_never_unloaded_or_reloaded(monkeypatch, entry):
    sf = _SF(monkeypatch, status=[{"model_id": SID_A, **entry}])
    with pytest.raises(sc.NotMine):
        sf.rig.unload(SID_A, "a")
    with pytest.raises(sc.NotMine):
        sf.rig.load(_copy_a(), 73728)              # load_exact would unload a misshapen resident
    assert not sf.sent("/unload") and not sf.sent("/load")


def test_rule4_every_load_names_ctx_size_and_slots(monkeypatch):
    sf = _SF(monkeypatch)
    sf.rig.load(_copy_a(), 73728)
    (_, _, _, body), = sf.sent("/load")
    assert body["ctx_size"] == 73728 and body["parallel"] == 4 and body["devices"] == [0, 1]
    assert body["kv_cache_type"] == "q8_0"


def test_copy_addressing_is_one_function():
    assert sc.copy_serve_id(MID, "a") == SID_A
    assert sc.copy_serve_id(MID, "b", {"serve_id": MID + ":2"}) == MID + ":2"
