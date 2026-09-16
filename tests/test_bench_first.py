"""Bench-first policy (2026-09-08): standing force, planner-driven device
choice, and vacating a foreign render lease through ClawForge."""
import pytest

from crucibleforge import studioforge
from crucibleforge.providers import get_provider


def _cfg(**prov_extra):
    prov = {"type": "studioforge", "base_url": "http://rig:1234/v1", "lease": True,
            "lease_devices": [0, 1, 2, 3]}
    prov.update(prov_extra)
    return {"providers": {"studioforge": prov}, "models": [], "judge": {"candidates": [
        {"provider": "studioforge", "model_id": "x/y/z"}]}}


def test_provider_reads_policy_keys():
    p = get_provider(_cfg(force_evict=True, lease_devices_preferred=[0, 1],
                          clawforge_mcp="http://rig:8700/mcp"), "studioforge")
    assert p.force_evict_policy is True
    assert p.lease_devices_preferred == [0, 1]
    assert p.clawforge_mcp == "http://rig:8700/mcp"
    q = get_provider(_cfg(), "studioforge")
    assert q.force_evict_policy is False and q.lease_devices_preferred is None and q.clawforge_mcp is None


def test_guard_applies_standing_force(monkeypatch):
    from crucibleforge import cli
    cfg = _cfg(force_evict=True)
    p = get_provider(cfg, "studioforge")
    monkeypatch.setattr(p, "snapshot", lambda: None)
    cli._ProviderGuard(cfg, [], include_judge=True)
    assert p.force_evict is True
    assert cli._judge_policy_force(cfg) is True
    cfg2 = _cfg()
    p2 = get_provider(cfg2, "studioforge")
    monkeypatch.setattr(p2, "snapshot", lambda: None)
    cli._ProviderGuard(cfg2, [], include_judge=True)
    assert p2.force_evict is False
    assert cli._judge_policy_force(cfg2) is False


def _options_payload(fits_on_5090s):
    opt = {"fits": True, "devices": [0, 1]} if fits_on_5090s else None
    return 200, {"model": {"placements": [
        {"mode": "dual_5090", "devices": [0, 1], "optimal": opt},
        {"mode": "all_gpus", "devices": [0, 1, 2, 3], "optimal": {"fits": True}},
    ]}}


def test_choose_devices_prefers_5090s_when_planner_says_fit(monkeypatch):
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: _options_payload(True))
    assert studioforge.choose_lease_devices("u", "", None, "m", [0, 1], [0, 1, 2, 3]) == [0, 1]
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: _options_payload(False))
    assert studioforge.choose_lease_devices("u", "", None, "m", [0, 1], [0, 1, 2, 3]) == [0, 1, 2, 3]
    # planner silent → whole rig, never a guess
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: (500, {}))
    assert studioforge.choose_lease_devices("u", "", None, "m", [0, 1], [0, 1, 2, 3]) == [0, 1, 2, 3]
    # no preference configured → no planner call at all
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: pytest.fail("must not call"))
    assert studioforge.choose_lease_devices("u", "", None, "m", None, [0, 1, 2, 3]) == [0, 1, 2, 3]


def test_foreign_render_lease_filter():
    leases = [
        {"id": "a", "holder": "clawforge2", "holder_family": "clawforge", "kind": "render", "devices": [2]},
        {"id": "b", "holder": "crucibleforge-judge", "holder_family": "crucibleforge", "kind": "benchmark", "devices": [0, 1, 2, 3]},
        {"id": "c", "holder": "someone", "holder_family": "other", "kind": "agent", "devices": [3]},
    ]
    assert [l["id"] for l in studioforge.foreign_render_leases(leases, [0, 1, 2, 3])] == ["a"]
    assert studioforge.foreign_render_leases(leases, [0, 1]) == []


def test_vacate_waits_for_release_and_refuses_without_url(monkeypatch):
    calls = []
    state = {"n": 0}
    render = [{"id": "a", "holder": "clawforge2", "holder_family": "clawforge", "kind": "render", "devices": [2]}]

    def fake_list(*a, **k):
        state["n"] += 1
        return render if state["n"] < 3 else []
    monkeypatch.setattr(studioforge, "list_leases", fake_list)
    monkeypatch.setattr(studioforge, "clawforge_control", lambda url, action, **k: calls.append((url, action)) or {})
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: None)
    assert studioforge.vacate_render_leases("u", "", None, [0, 1, 2, 3], "http://cf/mcp") is True
    assert calls == [("http://cf/mcp", "vacate")]
    # cards the render lease does not touch → nothing to do, no call
    state["n"] = 0
    calls.clear()
    assert studioforge.vacate_render_leases("u", "", None, [0, 1], "http://cf/mcp") is False
    assert calls == []
    # a render lease in the way and no ClawForge URL → loud refusal
    state["n"] = 0
    with pytest.raises(studioforge.StudioForgeError, match="clawforge_mcp"):
        studioforge.vacate_render_leases("u", "", None, [0, 1, 2, 3], None)
    # never released → loud, not "run anyway"
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: render)
    with pytest.raises(studioforge.StudioForgeError, match="still stand"):
        studioforge.vacate_render_leases("u", "", None, [2], "http://cf/mcp", wait_s=0.01)


def test_release_resumes_clawforge_only_if_we_vacated(monkeypatch):
    p = get_provider(_cfg(clawforge_mcp="http://cf/mcp"), "studioforge")
    calls = []
    monkeypatch.setattr(studioforge, "clawforge_control", lambda url, action, **k: calls.append(action) or {})
    monkeypatch.setattr(studioforge, "release_lease", lambda *a, **k: True)
    p._lease = {"_lease_id": "L", "_model_id": "m"}
    p.release_lease()
    assert calls == []
    p._lease = {"_lease_id": "L", "_model_id": "m"}
    p._vacated_render = True
    p.release_lease()
    assert calls == ["resume"]
    assert p._vacated_render is False


def test_busy_refusal_unloads_after_grace(monkeypatch):
    """A model_busy refusal that persists past busy_unload_after_s makes the
    client unload the busy resident once and keep claiming; without the
    option it just waits."""
    calls = {"post": 0, "unload": []}
    busy = (503, {"error": {"message": "x/y/z (1 in flight) is serving on CUDA [0, 1]; a lease never interrupts a stream (D36). Retry when it is idle.",
                            "code": "model_busy",
                            "studioforge": {"retry_after_s": 15.0, "busy_models": [{"model_id": "x/y/z", "active_requests": 1}]}},
                  "_retry_after_s": 15.0})

    def fake_mgmt(method, base, key, hdrs, path, json=None, timeout=None):
        if path == "/api/leases":
            calls["post"] += 1
            if calls["unload"]:
                return 200, {"lease_id": "L1"}
            return busy
        if path.endswith("/unload"):
            calls["unload"].append(path)
            return 200, {}
        raise AssertionError(path)
    monkeypatch.setattr(studioforge, "_mgmt", fake_mgmt)
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: None)
    out = studioforge.acquire_lease("u", "", None, [0, 1], model_ids=["m"], force=True,
                                    wait_busy_s=600, busy_unload_after_s=60)
    assert out["_lease_id"] == "L1"
    assert len(calls["unload"]) == 1 and calls["unload"][0].endswith("x%2Fy%2Fz/unload")
    assert calls["post"] >= 5          # waited the grace (4×15s) before cutting
    # without the option: never unloads, waits out the budget, then fails
    calls["post"] = 0
    calls["unload"].clear()
    with pytest.raises(studioforge.StudioForgeError):
        studioforge.acquire_lease("u", "", None, [0, 1], model_ids=["m"], force=True,
                                  wait_busy_s=45, busy_unload_after_s=None)
    assert calls["unload"] == []


def test_busy_models_parses_both_shapes():
    assert studioforge._busy_models({"error": {"studioforge": {"busy_models": [{"model_id": "a/b"}]}}}) == ["a/b"]
    assert studioforge._busy_models({"error": {"message": "a/b (2 in flight) is serving on CUDA [0]"}}) == ["a/b"]
    assert studioforge._busy_models({"error": {"message": "pinned model(s) …"}}) == []


def test_link_check_canary_never_jit_loads_in_lease_mode(monkeypatch):
    from crucibleforge import preflight
    p = get_provider(_cfg(), "studioforge")           # lease: True
    monkeypatch.setattr(studioforge, "list_models_full",
                        lambda *a, **k: pytest.fail("must not consult the catalog in lease mode"))
    monkeypatch.setattr(studioforge, "residents",
                        lambda *a, **k: [{"model_id": "r/esident", "state": "ready"}])
    assert preflight._pick_studioforge_canary({}, p, ["x/y/benched"]) == "r/esident"
    monkeypatch.setattr(studioforge, "residents", lambda *a, **k: [])
    assert preflight._pick_studioforge_canary({}, p, ["x/y/benched"]) is None
    # an explicit override still wins
    assert preflight._pick_studioforge_canary({"link_check": {"canary_model_id": "o/v"}}, p, ["x"]) == "o/v"
