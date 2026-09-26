"""The rig recommends 1 slot for some models; a benchmark widens the load."""
from crucibleforge import studioforge as sf


def _patch(monkeypatch, refuse_above):
    calls = []
    state = {"parallel": 1}

    def mgmt(method, base, key, headers, path, json=None, timeout=None):
        calls.append((path.rsplit("/", 1)[-1], dict(json or {})))
        if path.endswith("/load"):
            if json["parallel"] > refuse_above:
                return 507, {"error": "insufficient_vram"}
            state["parallel"] = json["parallel"]
        return 200, {}

    monkeypatch.setattr(sf, "_mgmt", mgmt)
    monkeypatch.setattr(sf, "wait_ready", lambda *a, **k: None)
    monkeypatch.setattr(sf, "warm_model", lambda *a, **k: 0.0)
    monkeypatch.setattr(sf, "load_recommended", lambda *a, **k: {})
    monkeypatch.setattr(sf, "loaded_plan", lambda *a, **k: {
        "parallel": state["parallel"], "ctx_size": 32768, "devices": [0, 1]})
    return calls


def test_widens_to_widest_that_fits(monkeypatch):
    calls = _patch(monkeypatch, refuse_above=6)
    live = {"parallel": 1, "ctx_size": 32768, "devices": [0, 1], "kv_cache_type": "f16"}
    new = sf._widen_slots("m", "u", "", None, live, 32768, 4, 8)
    assert new["parallel"] == 6
    loads = [b for p, b in calls if p == "load"]
    assert [b["parallel"] for b in loads] == [8, 6]
    assert all(b["ctx_size"] == 32768 and b["devices"] == [0, 1] for b in loads)


def test_nothing_fits_restores_and_returns_none(monkeypatch):
    _patch(monkeypatch, refuse_above=1)
    live = {"parallel": 1, "ctx_size": 32768, "devices": [0, 1]}
    assert sf._widen_slots("m", "u", "", None, live, 32768, 4, 8) is None


def test_f16_refused_then_q8_kv_widens(monkeypatch):
    """2026-09-26 precog 123B: every width refused at f16 KV (one slot on four
    cards); the same widths are retried with a q8_0 KV cache before giving up."""
    calls = []
    state = {"parallel": 1}

    def mgmt(method, base, key, headers, path, json=None, timeout=None):
        calls.append((path.rsplit("/", 1)[-1], dict(json or {})))
        if path.endswith("/load"):
            limit = 3 if json.get("kv_cache_type") == "q8_0" else 1
            if json["parallel"] > limit:
                return 507, {"error": "insufficient_vram"}
            state["parallel"] = json["parallel"]
        return 200, {}

    monkeypatch.setattr(sf, "_mgmt", mgmt)
    monkeypatch.setattr(sf, "wait_ready", lambda *a, **k: None)
    monkeypatch.setattr(sf, "warm_model", lambda *a, **k: 0.0)
    monkeypatch.setattr(sf, "load_recommended", lambda *a, **k: {})
    monkeypatch.setattr(sf, "loaded_plan", lambda *a, **k: {
        "parallel": state["parallel"], "ctx_size": 32768, "devices": [0, 1, 2, 3]})
    live = {"parallel": 1, "ctx_size": 32768, "devices": [0, 1, 2, 3], "kv_cache_type": "f16"}
    new = sf._widen_slots("m", "u", "", None, live, 32768, 4, 8)
    assert new["parallel"] == 3
    loads = [b for p, b in calls if p == "load"]
    assert [(b["parallel"], b["kv_cache_type"]) for b in loads] == [
        (8, "f16"), (6, "f16"), (4, "f16"), (3, "f16"), (2, "f16"),
        (8, "q8_0"), (6, "q8_0"), (4, "q8_0"), (3, "q8_0")]
    assert loads[-1]["kv_cache_type_v"] == "q8_0" and loads[-1]["devices"] == [0, 1, 2, 3]


def test_already_q8_does_not_retry(monkeypatch):
    calls = _patch(monkeypatch, refuse_above=1)
    live = {"parallel": 1, "ctx_size": 32768, "devices": [0, 1], "kv_cache_type": "q8_0"}
    assert sf._widen_slots("m", "u", "", None, live, 32768, 4, 8) is None
    assert len([p for p, _ in calls if p == "load"]) == 5          # one pass only
