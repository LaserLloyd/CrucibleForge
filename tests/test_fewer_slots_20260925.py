"""The rig can recommend 8 slots and then refuse its own load by a few GiB;
the client retries at the slot count the refusal suggests."""
from crucibleforge import studioforge as sf

REFUSAL = {"_status": 507, "error": {"code": "insufficient_vram", "message":
           "Cannot load 'm' entirely in VRAM: needs 60.43 GiB, 55.32 GiB usable. "
           "Suggestions: reduce parallel from 8 to 7: 7 slot(s) at 16384 tokens fit"}}


def test_hint_parsed():
    assert sf._fewer_slots_hint(REFUSAL) == 7
    assert sf._fewer_slots_hint({"_status": 503, "error": "reduce parallel from 8 to 7"}) is None
    assert sf._fewer_slots_hint({"_status": 507, "error": "no fit"}) is None


def test_load_retries_with_max_slots(monkeypatch):
    calls = []

    def fake_lr(model_id, base, key, ctx, prefer_mode=None, headers=None, max_slots=None):
        calls.append(max_slots)
        return dict(REFUSAL) if max_slots is None else {"plan": {"parallel": 7, "ctx_size": ctx}}

    monkeypatch.setattr(sf, "load_recommended", fake_lr)
    monkeypatch.setattr(sf, "loaded_plan", lambda *a, **k: None if not calls else
                        {"state": "ready", "parallel": 7, "ctx_size": 16384, "devices": [0, 1]})
    monkeypatch.setattr(sf, "wait_ready", lambda *a, **k: None)
    monkeypatch.setattr(sf, "warm_model", lambda *a, **k: 0.0)
    sf.load_model("m", "u", "", 16384, recommended=True, min_slots=4)
    assert calls == [None, 7]
