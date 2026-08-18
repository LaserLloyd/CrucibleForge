"""Pre-flight (provider data-channel probe) — no network."""
import pytest

from gauntlet import preflight, providers
from gauntlet.api import TransportError


def _cfg():
    return {"providers": {"remote": {"type": "openai", "base_url": "http://x/v1"},
                          "local": {"type": "lmstudio", "base_url": "http://l/v1"},
                          "sf": {"type": "studioforge", "base_url": "http://s/v1"}},
            "defaults": {}, "judge": {"candidates": []}, "models": []}


@pytest.fixture(autouse=True)
def _clear():
    providers.clear_cache()
    yield
    providers.clear_cache()


def test_lmstudio_entries_are_skipped(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    monkeypatch.setattr(providers.Provider, "chat", lambda self, *a, **k: calls.append(1))
    preflight.check_link_health(_cfg(), [{"provider": "local", "model_id": "m"}])
    assert calls == []


def test_openai_probe_each_model(monkeypatch):
    seen = []
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    monkeypatch.setattr(providers.Provider, "chat",
                        lambda self, model_id, *a, **k: seen.append(model_id))
    preflight.check_link_health(_cfg(), [{"provider": "remote", "model_id": "a"},
                                         {"provider": "remote", "model_id": "b"}])
    assert seen == ["a", "b"]


def test_unreachable_provider_aborts(monkeypatch):
    monkeypatch.setattr(providers.Provider, "alive", lambda self: False)
    with pytest.raises(SystemExit) as e:
        preflight.check_link_health(_cfg(), [{"provider": "remote", "model_id": "a"}])
    assert "unreachable" in str(e.value)


def test_probe_retries_once_then_aborts(monkeypatch):
    calls = []

    def boom(self, *a, **k):
        calls.append(1)
        raise TransportError("peer closed connection")
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    monkeypatch.setattr(providers.Provider, "chat", boom)
    with pytest.raises(SystemExit) as e:
        preflight.check_link_health(_cfg(), [{"provider": "remote", "model_id": "a"}])
    assert len(calls) == 2
    assert "data channel" in str(e.value)


def test_probe_passes_on_second_try(monkeypatch):
    calls = []

    def flaky(self, *a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise TransportError("blip")
        return object()
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    monkeypatch.setattr(providers.Provider, "chat", flaky)
    preflight.check_link_health(_cfg(), [{"provider": "remote", "model_id": "a"}])
    assert len(calls) == 2


def test_studioforge_probes_smallest(monkeypatch):
    from gauntlet import studioforge
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    monkeypatch.setattr(studioforge, "list_models_full", lambda b, k: [
        {"id": "big", "studioforge": {"size_bytes": 30, "kind": "llm"}},
        {"id": "small", "studioforge": {"size_bytes": 3, "kind": "llm"}},
        {"id": "emb", "studioforge": {"size_bytes": 1, "kind": "embedding"}}])
    seen = []
    monkeypatch.setattr(providers.Provider, "chat",
                        lambda self, model_id, *a, **k: seen.append(model_id))
    preflight.check_link_health(_cfg(), [{"provider": "sf", "model_id": "big"},
                                         {"provider": "sf", "model_id": "small"},
                                         {"provider": "sf", "model_id": "emb"}])
    assert seen == ["small"]


def test_canary_override_wins(monkeypatch):
    cfg = _cfg()
    cfg["link_check"] = {"canary_model_id": "forced"}
    prov = providers.get_provider(cfg, "sf")
    assert preflight._pick_studioforge_canary(cfg, prov, ["a", "b"]) == "forced"
