"""Wider placement (2026-11-13): Nanbeige4.3-33B Q8_0 fitted only 2 slots at
ctx 73728 on the two 5090s (q8_0 KV ~113 KB/token, full attention) while both
3090s sat idle — 32 thinking jobs queued two at a time for 4.7 h. Below
min_slots the provider now asks the rig's dry-run planner what wider card sets
hold at the SAME ctx and moves only for a clear aggregate-throughput gain."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from crucibleforge import providers, studioforge as sf

M = "bartowski/Nanbeige_Nanbeige4.3-33B-GGUF/Nanbeige_Nanbeige4.3-33B-Q8_0"

# the rig's own figures for Nanbeige on 2026-11-13 (GET /options est_gen_tps)
SPEEDS = {(0, 1): 33.8, (2, 3): 18.1, (0, 1, 2): 25.1, (0, 1, 2, 3): 21.3}


# ------------------------------------------------------------ pure choice

def test_nanbeige_numbers_pick_all_four_cards():
    cur = {"devices": [0, 1], "slots": 2, "tps": 33.8}
    cands = [{"devices": [0, 1, 2], "slots": 5, "kv": "q8_0", "tps": 25.1},
             {"devices": [0, 1, 3], "slots": 5, "kv": "q8_0", "tps": 21.3},
             {"devices": [0, 1, 2, 3], "slots": 7, "kv": "q8_0", "tps": 21.3}]
    got = sf.choose_wider_placement(cur, cands, cap_slots=8)
    assert got["devices"] == [0, 1, 2, 3] and got["slots"] == 7
    assert got["gain"] == pytest.approx(7 * 21.3 / (2 * 33.8))


def test_slower_cards_for_a_slot_or_two_keep_the_5090_plan():
    # 3 slots at 3090-dragged speed (63.9) vs 2 fast ones (67.6): no move
    cur = {"devices": [0, 1], "slots": 2, "tps": 33.8}
    cands = [{"devices": [0, 1, 2, 3], "slots": 3, "kv": "q8_0", "tps": 21.3}]
    assert sf.choose_wider_placement(cur, cands) is None
    # a gain, but under the 1.5x bar: still no move
    cands = [{"devices": [0, 1, 2, 3], "slots": 4, "kv": "q8_0", "tps": 21.3}]
    assert sf.choose_wider_placement(cur, cands) is None


def test_tie_goes_to_fewer_cards_and_slots_are_capped():
    cur = {"devices": [0, 1], "slots": 1, "tps": 30.0}
    cands = [{"devices": [0, 1, 2, 3], "slots": 12, "kv": "q8_0", "tps": 20.0},
             {"devices": [0, 1, 2], "slots": 8, "kv": "q8_0", "tps": 20.0}]
    got = sf.choose_wider_placement(cur, cands, cap_slots=8)
    assert got["devices"] == [0, 1, 2]          # 12 slots count as 8: a tie


def test_candidates_without_slots_or_speed_are_ignored():
    cur = {"devices": [0, 1], "slots": 1, "tps": 30.0}
    assert sf.choose_wider_placement(cur, [{"devices": [0, 1, 2], "slots": 0, "tps": 20},
                                           {"devices": [0, 1, 3], "slots": 6, "tps": None}]) is None


def test_per_slot_speed_exact_then_slowest_superset():
    assert sf.per_slot_speed(SPEEDS, [1, 0]) == 33.8
    assert sf.per_slot_speed({(0, 1): 33.8, (0, 1, 2, 3): 21.3, (0, 1, 2, 4): 19.0},
                             [0, 1, 2]) == 19.0
    assert sf.per_slot_speed({(0, 1): 33.8}, [0, 1, 3]) is None


def test_placement_speeds_reads_placements_and_options(monkeypatch):
    body = {"model": {
        "placements": [{"devices": [0, 1], "optimal": {"devices": [0, 1], "est_gen_tps": 33.8}},
                       {"devices": [0], "optimal": None}],
        "options": [{"devices": [1, 0], "est_gen_tps": 33.3},
                    {"devices": [1, 0, 2], "est_gen_tps": 25.1}]}}
    monkeypatch.setattr(sf, "_mgmt", lambda *a, **k: (200, body))
    assert sf.placement_speeds("u", "", None, M) == {(0, 1): 33.8, (0, 1, 2): 25.1}


# ------------------------------------------------------------ dry run

def _plan_mgmt(monkeypatch, total_for, available, seen=None):
    """A /plan dry run: fits when total(parallel, kv) <= available bytes."""
    def mgmt(method, base, key, headers, path, json=None, timeout=None):
        assert method == "GET" and "/plan?" in path, path
        q = parse_qs(urlparse(path).query)
        if seen is not None:
            seen.append(q)
        need = total_for(int(q["parallel"][0]), q["kv_cache_type"][0],
                         tuple(int(d) for d in q["devices"]))
        avail = available(tuple(int(d) for d in q["devices"]))
        if need <= avail:
            return 200, {"fits": True}
        return 200, {"fits": False, "shortfall_bytes": need - avail}
    monkeypatch.setattr(sf, "_mgmt", mgmt)


def test_dry_run_credits_the_models_own_bytes(monkeypatch):
    seen = []
    _plan_mgmt(monkeypatch, lambda p, kv, d: 50 + 10 * p, lambda d: 40, seen)
    assert sf.dry_run_fits("u", "", None, M, [0, 1, 2], 73728, "q8_0", 3, credit_bytes=40)
    assert not sf.dry_run_fits("u", "", None, M, [0, 1, 2], 73728, "q8_0", 3, credit_bytes=39)
    q = seen[0]
    assert q["devices"] == ["0", "1", "2"] and q["ctx_size"] == ["73728"]
    assert q["kv_cache_type"] == q["kv_cache_type_v"] == ["q8_0"]


def test_dry_run_refusal_without_a_shortfall_is_a_no(monkeypatch):
    monkeypatch.setattr(sf, "_mgmt", lambda *a, **k: (200, {"fits": False, "reason": "leased"}))
    assert not sf.dry_run_fits("u", "", None, M, [0, 1], 8192, "f16", 2, credit_bytes=10**12)
    monkeypatch.setattr(sf, "_mgmt", lambda *a, **k: (400, {}))
    assert not sf.dry_run_fits("u", "", None, M, [0, 1], 8192, "f16", 2)


def test_max_slots_tries_widest_first(monkeypatch):
    seen = []
    _plan_mgmt(monkeypatch, lambda p, kv, d: 10 * p, lambda d: 55, seen)
    assert sf.max_slots_dry_run("u", "", None, M, [0, 1], 4096, "f16", range(3, 9)) == 5
    assert [int(q["parallel"][0]) for q in seen] == [8, 7, 6, 5]
    assert sf.max_slots_dry_run("u", "", None, M, [0, 1], 4096, "f16", [9, 10]) == 0


# ------------------------------------------------------------ provider

def _prov(**kw):
    kw.setdefault("lease_devices", [0, 1, 2, 3])
    return providers.Provider(name="sf", type="studioforge", base_url="http://sf.invalid/v1",
                              lease=True, **kw)


def test_wants_wider_only_below_min_slots_for_thinking_models(monkeypatch):
    p = _prov()
    p._lease = {"_devices": [0, 1]}
    p._plans[M] = {"parallel": 2}
    monkeypatch.setattr(p, "model_capabilities", lambda m: {"thinking", "tools"})
    assert p._wants_wider_devices(M)
    monkeypatch.setattr(p, "model_capabilities", lambda m: {"tools"})
    assert not p._wants_wider_devices(M)
    p.widen_devices = "always"
    assert p._wants_wider_devices(M)
    p._plans[M] = {"parallel": 4}
    assert not p._wants_wider_devices(M)              # min_slots (4) reached
    p._plans[M] = {"parallel": 2}
    p.widen_devices = "never"
    assert not p._wants_wider_devices(M)
    p.widen_devices = "always"
    p.day_single_card = True
    assert not p._wants_wider_devices(M)              # day mode: one card, by contract


def test_wants_wider_skips_when_capabilities_unreadable(monkeypatch):
    p = _prov()
    p._lease = {"_devices": [0, 1]}
    p._plans[M] = {"parallel": 1}

    def boom(m):
        raise sf.StatusUnavailable("down")
    monkeypatch.setattr(p, "model_capabilities", boom)
    assert not p._wants_wider_devices(M)


def test_wants_wider_skips_a_lease_that_already_names_every_allowed_card(monkeypatch):
    p = _prov(lease_devices=[0, 1])
    p._lease = {"_devices": [0, 1]}
    p._plans[M] = {"parallel": 1}

    def no_network(m):
        raise AssertionError("no capability lookup needed")
    monkeypatch.setattr(p, "model_capabilities", no_network)
    assert not p._wants_wider_devices(M)
    p._plans[M] = {}                                  # no live plan: nothing to judge
    p.lease_devices = [0, 1, 2, 3]
    assert not p._wants_wider_devices(M)


def test_spare_cards_never_take_a_leased_or_occupied_card(monkeypatch):
    leases = [{"holder": "speechforge", "holder_family": "speechforge", "devices": [3]},
              {"holder": "crucibleforge", "devices": [0, 1]}]
    monkeypatch.setattr(sf, "list_leases", lambda *a, **k: leases)
    monkeypatch.setattr(sf, "status", lambda *a, **k: {"loaded": [
        {"model_id": M, "plan": {"devices": [0, 1]}},
        {"model_id": "embedder", "plan": {"devices": [2]}}]})
    assert _prov()._spare_cards(M, [0, 1]) == []
    monkeypatch.setattr(sf, "status", lambda *a, **k: {"loaded": [
        {"model_id": M, "plan": {"devices": [0, 1]}}]})
    assert _prov()._spare_cards(M, [0, 1]) == [2]
    # a render lease is NOT vacated for a widening (it is for the base claim)
    leases[0] = {"holder": "render-tenant", "kind": "render", "devices": [2]}
    assert _prov(render_vacate_mcp="http://cf.invalid/mcp")._spare_cards(M, [0, 1]) == [3]


@pytest.fixture
def rig(monkeypatch):
    """Nanbeige on a fake rig: 2 slots on [0,1]; [0,1,2,3] holds 7 at q8_0."""
    calls = {"acquire": [], "release": [], "unload": [], "widths": [], "load_model": []}
    GB = 10**9

    def total(p, kv, devs):
        per_tok = 0.1027 if kv == "q8_0" else 0.1933        # GB per token, measured
        fixed = 39.9 if len(devs) == 2 else 42.0 + (len(devs) - 3) * 0.6
        return int((fixed + per_tok * 73728 * p / 1000) * GB)

    def avail(devs):                       # usable after headroom, model resident on 0/1
        cap = {0: 1.5, 1: 1.5, 2: 22.9, 3: 22.3}
        return int(sum(cap[d] for d in devs) * GB)

    _plan_mgmt(monkeypatch, total, avail)
    monkeypatch.setattr(sf, "placement_speeds", lambda *a, **k: dict(SPEEDS))
    monkeypatch.setattr(sf, "own_vram_bytes", lambda *a, **k: {0: 28 * GB, 1: 26 * GB})
    monkeypatch.setattr(sf, "list_leases", lambda *a, **k: [])
    monkeypatch.setattr(sf, "status", lambda *a, **k: {"loaded": [
        {"model_id": M, "plan": {"devices": [0, 1]}}]})

    ids = iter(f"L{i}" for i in range(1, 100))

    def acquire(base, key, hdrs, devices, **kw):
        calls["acquire"].append(list(devices))
        return {"_lease_id": next(ids)}

    monkeypatch.setattr(sf, "acquire_lease", acquire)
    monkeypatch.setattr(sf, "release_lease",
                        lambda b, k, h, lid: calls["release"].append(lid) or True)
    monkeypatch.setattr(sf, "unload_model",
                        lambda b, k, h, m: calls["unload"].append(m) or True)
    monkeypatch.setattr(sf, "wait_ready", lambda *a, **k: {})

    def try_widths(model_id, base, key, hdrs, body, widths, have):
        calls["widths"].append((dict(body), list(widths), have))
        return calls.get("try_result", {"parallel": widths[0], "ctx_size": body["ctx_size"],
                                        "devices": body["devices"],
                                        "kv_cache_type": body["kv_cache_type"]})

    monkeypatch.setattr(sf, "_try_widths", try_widths)

    def load_model(*a, **k):
        calls["load_model"].append(a[0])
        return 1.0

    monkeypatch.setattr(sf, "load_model", load_model)
    return calls


def _leased_provider(calls):
    p = _prov(target_slots=8)
    p._start_keepalive = lambda: None
    p._acquire(M, [0, 1])
    p._plans[M] = {"parallel": 2, "ctx_size": 73728, "devices": [0, 1], "kv_cache_type": "q8_0"}
    calls["acquire"].clear()
    return p


def test_nanbeige_moves_to_four_cards_with_seven_slots(rig, monkeypatch):
    p = _leased_provider(rig)
    monkeypatch.setattr(p, "_remember_plan",
                        lambda m, live=None: p._plans.__setitem__(m, dict(live or {})))
    p._widen_devices(M, 73728)
    assert rig["unload"] == [M] and rig["release"] == ["L1"]
    assert rig["acquire"] == [[0, 1, 2, 3]]
    body, widths, have = rig["widths"][0]
    assert body == {"ctx_size": 73728, "devices": [0, 1, 2, 3],
                    "kv_cache_type": "q8_0", "kv_cache_type_v": "q8_0"}
    assert widths == [7, 6, 5, 4, 3] and have == 2
    assert p._plans[M]["parallel"] == 7 and p._plans[M]["devices"] == [0, 1, 2, 3]
    assert p._lease["_devices"] == [0, 1, 2, 3]
    assert rig["load_model"] == []          # no fallback reload


def test_failed_move_goes_back_to_the_original_cards(rig, monkeypatch):
    p = _leased_provider(rig)
    monkeypatch.setattr(p, "_remember_plan", lambda m, live=None: None)
    rig["try_result"] = None
    p._widen_devices(M, 73728)
    assert rig["acquire"] == [[0, 1, 2, 3], [0, 1]]
    assert rig["release"] == ["L1", "L2"]       # the [0,1] lease, then the wide one
    assert rig["load_model"] == [M]          # the normal load path, at the run's ctx
    assert p._lease["_devices"] == [0, 1]


def test_no_clear_gain_touches_nothing(rig, monkeypatch):
    p = _leased_provider(rig)
    # slow wide sets: [0,1,2] 5 x 12.0 = 60 and [0,1,2,3] 7 x 9.0 = 63 tok/s vs
    # 2 x 33.8 = 67.6 now -- no wider set clears the 1.5x bar
    monkeypatch.setattr(sf, "placement_speeds",
                        lambda *a, **k: {(0, 1): 33.8, (0, 1, 2): 12.0, (0, 1, 2, 3): 9.0})
    p._widen_devices(M, 73728)
    assert rig["unload"] == [] and rig["acquire"] == [] and rig["release"] == []


def test_lease_load_widens_after_a_short_load(rig, monkeypatch):
    p = _prov(target_slots=8)
    p._start_keepalive = lambda: None
    monkeypatch.setattr(p, "claim_devices", lambda m, exclude=(): [0, 1])
    monkeypatch.setattr(sf, "release_stale_leases", lambda *a, **k: None)
    monkeypatch.setattr(p, "model_capabilities", lambda m: {"thinking"})
    state = {"plan": {"parallel": 2, "ctx_size": 73728, "devices": [0, 1],
                      "kv_cache_type": "q8_0", "state": "ready"}}
    monkeypatch.setattr(sf, "loaded_plan", lambda *a, **k: dict(state["plan"]))

    def try_widths(model_id, base, key, hdrs, body, widths, have):
        state["plan"] = {"parallel": widths[0], "ctx_size": body["ctx_size"],
                         "devices": body["devices"], "kv_cache_type": body["kv_cache_type"],
                         "state": "ready"}
        return dict(state["plan"])

    monkeypatch.setattr(sf, "_try_widths", try_widths)
    p._lease_load(M, 73728)
    plan = p.loaded_plan_for(M)
    assert plan["parallel"] == 7 and plan["devices"] == [0, 1, 2, 3]
    assert plan["ctx_size"] == 73728                    # the window never shrinks


def test_lease_load_does_not_widen_a_model_that_already_has_its_slots(rig, monkeypatch):
    p = _prov(target_slots=8)
    p._start_keepalive = lambda: None
    monkeypatch.setattr(p, "claim_devices", lambda m, exclude=(): [0, 1])
    monkeypatch.setattr(sf, "release_stale_leases", lambda *a, **k: None)
    monkeypatch.setattr(p, "model_capabilities", lambda m: {"thinking"})
    monkeypatch.setattr(sf, "loaded_plan", lambda *a, **k: {
        "parallel": 6, "ctx_size": 73728, "devices": [0, 1], "state": "ready"})
    p._lease_load(M, 73728)
    assert rig["acquire"] == [[0, 1]] and rig["widths"] == []


def test_widen_devices_is_read_from_models_yaml():
    cfg = {"providers": {"sf": {"type": "studioforge", "base_url": "http://sf.invalid/v1",
                                "lease": True, "widen_devices": "Never"}},
           "defaults": {}, "models": [], "judge": {"candidates": []}}
    providers.clear_cache()
    try:
        assert providers.get_provider(cfg, "sf").widen_devices == "never"
    finally:
        providers.clear_cache()
