"""A provider restricted to named cards loads on exactly its leased cards (2026-11-13 23:11).

The first real bench45 reference run held a lease on the 3090 pair [2,3] (holder mblab-rigtput-d), but the
lease did not bring the model up and the fallback load-recommended planned it onto the 5090s [0,1]. A
provider with ``lease_devices`` now loads with ``load_exact`` on the leased cards and the run's KV cache type,
and fails closed (unload + error) whenever the model serves outside its lease."""
from __future__ import annotations

import pytest

from crucibleforge import providers, studioforge as sf

M = "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q8_0"


def _prov(**kw):
    p = providers.Provider(name="studioforge", type="studioforge", base_url="http://rig/v1", api_key="k",
                           lease=True, lease_devices=[2, 3], target_slots=4, kv_cache_type="q8_0", exact_on_lease=True, **kw)
    p._lease = {"_model_id": M, "_devices": [2, 3]}
    return p


@pytest.fixture
def rig(monkeypatch):
    calls = {"exact": [], "recommended": 0, "unload": 0}
    state = {"plan": None}
    monkeypatch.setattr(sf, "wait_ready", lambda *a, **k: (_ for _ in ()).throw(sf.StudioForgeError("never")))

    def load_exact(model_id, base_url, api_key, headers, ctx, ladder, devices=None, kv=None):
        calls["exact"].append({"ctx": ctx, "ladder": list(ladder), "devices": devices, "kv": kv})
        state["plan"] = {"state": "ready", "ctx_size": ctx, "parallel": list(ladder)[0],
                         "devices": devices, "kv_cache_type": kv}
        return dict(state["plan"])

    def load_model(*a, **k):
        calls["recommended"] += 1
        state["plan"] = {"state": "ready", "ctx_size": 73728, "parallel": 5, "devices": [0, 1],
                         "kv_cache_type": "f16"}
        return 1.0

    def unload(*a, **k):
        calls["unload"] += 1
        state["plan"] = None
        return True

    monkeypatch.setattr(sf, "load_exact", load_exact)
    monkeypatch.setattr(sf, "load_model", load_model)
    monkeypatch.setattr(sf, "unload_model", unload)
    monkeypatch.setattr(sf, "loaded_plan", lambda *a, **k: state["plan"])
    return calls, state


def test_restricted_provider_loads_exactly_on_the_leased_cards_with_the_run_kv(rig):
    calls, state = rig
    p = _prov()
    p._load_under_lease(M, 73728)
    assert calls["recommended"] == 0
    assert calls["exact"] == [{"ctx": 73728, "ladder": [4, 3, 2, 1], "devices": [2, 3], "kv": "q8_0"}]
    assert p._plans[M]["devices"] == [2, 3] and p._plans[M]["kv_cache_type"] == "q8_0"


def test_a_model_serving_outside_its_lease_is_unloaded_and_refused(rig):
    calls, state = rig
    p = _prov()
    p._plans[M] = {"state": "ready", "devices": [0, 1], "kv_cache_type": "q8_0"}
    with pytest.raises(sf.StudioForgeError, match="outside the lease"):
        p._check_inside_lease(M)
    assert calls["unload"] == 1


@pytest.mark.archive_default   # no KV setting = the 4.1 default profile; a bare bench45 load enforces its q8_0 run setting
def test_unrestricted_provider_keeps_load_recommended(rig):
    calls, state = rig
    p = _prov()
    p.exact_on_lease = False
    p.kv_cache_type = None
    p._lease["_devices"] = [0, 1, 2, 3]        # the planner's [0,1] is inside this lease
    p._load_under_lease(M, 73728)
    assert calls["recommended"] == 1 and calls["exact"] == []


def test_guard_also_catches_load_recommended_outside_the_lease(rig):
    calls, state = rig
    p = _prov()
    p.exact_on_lease = False                   # the 23:11 incident: [2,3] lease, planner chose [0,1]
    with pytest.raises(sf.StudioForgeError, match="outside the lease"):
        p._load_under_lease(M, 73728)
    assert calls["unload"] == 1


def test_load_exact_does_not_keep_a_load_with_the_wrong_kv_or_cards(monkeypatch):
    live = {"state": "ready", "ctx_size": 73728, "parallel": 5, "devices": [0, 1], "kv_cache_type": "f16"}
    posted = []
    monkeypatch.setattr(sf, "loaded_plan", lambda *a, **k: dict(live))
    monkeypatch.setattr(sf, "_mgmt", lambda method, base, key, hdrs, path, **k:
                        (posted.append((path, k.get("json"))) or (200, {})))
    monkeypatch.setattr(sf, "wait_ready", lambda *a, **k: None)
    sf.load_exact(M, "http://rig/v1", "k", None, 73728, [5], devices=[2, 3], kv="q8_0")
    loads = [b for path, b in posted if path.endswith("/load")]
    assert loads and loads[0]["devices"] == [2, 3] and loads[0]["kv_cache_type"] == "q8_0"


def test_provider_config_reads_priority_and_kv():
    cfg = {"providers": {"studioforge": {"type": "studioforge", "base_url": "http://rig/v1",
                                         "request_priority": 3, "kv_cache_type": "Q8_0"}}}
    p = providers.build_provider(cfg, "studioforge") if hasattr(providers, "build_provider") else None
    if p is None:
        pytest.skip("no single-provider builder in this tree")
    assert p.request_priority == 3 and p.kv_cache_type == "q8_0"
