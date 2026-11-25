"""Docker assessment 2026-11-25, item 1: an OpenAI-shaped server at a local address (a container alias like
host.docker.internal, a LAN box, loopback) is LOCAL wherever hosted-vs-local is decided: no price is required,
nothing is booked to the spend cap, rows need no recorded effort to publish, the board does not call it hosted.
The provider entry's ``hosted:`` word wins (a paid API behind a private address)."""
from __future__ import annotations

import pytest

from crucibleforge import cli, disclosure, providers, report, spend


def _cfg(url, **extra):
    return {"defaults": {}, "providers": {"local": {"type": "openai", "base_url": url, **extra}},
            "models": [{"name": "x", "provider": "local", "model_id": "x"}]}


@pytest.mark.parametrize("url", ["http://host.docker.internal:1234/v1", "http://127.0.0.1:8080/v1",
                                 "http://192.168.200.7:1234/v1"])  # scrub-ok: made-up test address
def test_a_local_openai_server_needs_no_price(url):
    providers._CACHE.clear()
    cfg = _cfg(url)
    assert cli._unpriced_hosted(cfg, cfg["models"]) == []


def test_a_hosted_api_and_the_hosted_word_still_need_a_price():
    providers._CACHE.clear()
    cfg = _cfg("https://api.example.com/v1")
    assert cli._unpriced_hosted(cfg, cfg["models"]) == ["x"]
    providers._CACHE.clear()
    cfg = _cfg("http://192.168.200.7:1234/v1", hosted=True)  # scrub-ok: made-up test address
    assert cli._unpriced_hosted(cfg, cfg["models"]) == ["x"]


def test_the_effective_type_reads_the_address_and_the_hosted_word():
    assert spend.effective_type("openai", "http://host.docker.internal:1234/v1") == "local"
    assert spend.effective_type("openai", "https://api.example.com/v1") == "openai"
    assert spend.effective_type("openai", "http://127.0.0.1:1/v1", hosted=True) == "openai"
    assert spend.effective_type("studioforge", "http://127.0.0.1:1/v1") == "studioforge"


def test_a_local_server_row_publishes_without_an_effort_and_is_not_hosted_on_the_board():
    row = {"case_id": "C1"}
    meta = {"provider_type": "openai", "hosted": False}
    assert disclosure.publish_refusal(row, report._meta_ptype(meta)) is None
    assert disclosure.publish_refusal(row, report._meta_ptype({"provider_type": "openai"})) is not None
    assert report._is_api_model(meta, None) is False
    assert report._is_api_model({"provider_type": "openai"}, None) is True


def test_a_local_server_call_is_not_booked_as_hosted_spend(monkeypatch):
    p = providers.Provider(name="local", type="openai", base_url="http://host.docker.internal:1234/v1")
    assert p.is_hosted is False
    seen = []
    monkeypatch.setattr(spend, "check", lambda *a, **k: None)
    monkeypatch.setattr(spend, "release", lambda *a, **k: None)
    monkeypatch.setattr(spend, "reserve", lambda t, *a, **k: seen.append(t) or None)
    monkeypatch.setattr(spend, "record", lambda t, *a, **k: seen.append(t))
    from crucibleforge import api
    monkeypatch.setattr(api, "stream_chat_retried", lambda *a, **k: "result")
    p.prices["x"] = {"input": 1, "output": 1}
    assert p.chat("x", [{"role": "user", "content": "hi"}]) == "result"
    assert seen == ["local", "local"]
