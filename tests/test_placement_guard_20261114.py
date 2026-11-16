"""Fail closed before a send when the placement is lost (2026-11-14 02:10).

Another holder's forced unload of the same model id removed our instance on [2,3]; the run's transport
retries then reached an id that was not loaded and the rig JIT-loaded it at its planner defaults (ctx
262,144 x 1 slot). An exact-on-lease StudioForge provider now checks the live plan before EVERY send
(first try and each retry) and sends nothing when the model is not ready on exactly the plan it loaded."""
from __future__ import annotations

import pytest

from crucibleforge import api, providers, studioforge as sf

M = "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q8_0"
PLAN = {"state": "ready", "ctx_size": 73728, "parallel": 3, "devices": [2, 3], "kv_cache_type": "q8_0"}


def _prov(exact=True, devices=(2, 3)):
    p = providers.Provider(name="studioforge", type="studioforge", base_url="http://rig/v1", api_key="k",
                           lease=True, lease_devices=list(devices) if devices else None,
                           exact_on_lease=exact)
    p._plans[M] = dict(PLAN)
    return p


@pytest.fixture
def live(monkeypatch):
    state = {"plan": dict(PLAN), "sent": 0}
    monkeypatch.setattr(sf, "loaded_plan", lambda *a, **k: state["plan"])

    def stream_chat(*a, **k):
        state["sent"] += 1
        return "ok"
    monkeypatch.setattr(api, "stream_chat", stream_chat)
    return state


def _send(p):
    g = p._placement_guard(M)
    return api.stream_chat_retried("http://rig/v1", "k", M, [{"role": "user", "content": "x"}],
                                   pre_send=g)


def test_guard_lets_the_send_through_on_the_loaded_plan(live):
    assert _send(_prov()) == "ok" and live["sent"] == 1


@pytest.mark.parametrize("plan, why", [
    (None, "not loaded"),
    ({**PLAN, "state": "loading"}, "state"),
    ({**PLAN, "ctx_size": 262144, "parallel": 1}, "ctx_size"),
    ({**PLAN, "devices": [0, 1]}, "devices"),
    ({**PLAN, "parallel": 1}, "parallel"),
])
def test_guard_fails_closed_and_sends_nothing(live, plan, why):
    live["plan"] = plan
    with pytest.raises(sf.StudioForgeError, match=why):
        _send(_prov())
    assert live["sent"] == 0


def test_guard_fails_closed_on_unreadable_status(live, monkeypatch):
    monkeypatch.setattr(sf, "loaded_plan", lambda *a, **k: (_ for _ in ()).throw(sf.StatusUnavailable("down")))
    with pytest.raises(sf.StudioForgeError, match="unverifiable"):
        _send(_prov())
    assert live["sent"] == 0


def test_guard_device_order_does_not_matter(live):
    live["plan"] = {**PLAN, "devices": [3, 2]}
    assert _send(_prov()) == "ok"


def test_guard_off_without_exact_lease_or_without_our_plan(live):
    assert _prov(exact=False)._placement_guard(M) is None
    assert _prov(devices=None)._placement_guard(M) is None
    p = _prov()
    p._plans.clear()
    assert p._placement_guard(M) is None
