"""A gateway that answers 401/403 on /models is reachable, but `status` and
`models discover` must say it wants an API key (it used to read as plain UP).

The "server" is the in-process fake on an ephemeral loopback port
(tests/_fakesf.py); no real network.
"""
from __future__ import annotations

import argparse

import pytest

from _fakesf import FakeStudioForge
from crucibleforge import cli, providers


def _cfg(base_url, **prov):
    return {
        "_path": "models.yaml", "defaults": {},
        "providers": {"llamacpp": {"type": "openai", "base_url": base_url, **prov}},
        "models": [], "judge": {"candidates": []},
    }


@pytest.fixture(autouse=True)
def _fresh_providers(monkeypatch):
    providers.clear_cache()
    monkeypatch.delenv("STUDIOFORGE_API_KEY", raising=False)
    monkeypatch.delenv("MY_GATEWAY_KEY", raising=False)
    monkeypatch.setattr(providers.Provider, "loaded_models", lambda self: [])
    yield
    providers.clear_cache()


def _status(cfg, capsys):
    cli.cmd_status(argparse.Namespace(), cfg)
    return capsys.readouterr().out


def _provider_line(out):
    return next(l for l in out.splitlines() if l.strip().startswith("llamacpp"))


@pytest.mark.parametrize("code", [401, 403])
def test_status_keeps_up_but_says_it_wants_a_key(capsys, code):
    with FakeStudioForge() as sf:
        sf.route("GET", "/v1/models", code, {"error": "unauthorized"})
        out = _status(_cfg(sf.url + "/v1"), capsys)
    assert " UP " in _provider_line(out)
    assert (f"reachable, but it wants an API key: set $STUDIOFORGE_API_KEY "
            f"and add api_key_env: STUDIOFORGE_API_KEY to the provider in models.yaml "
            f"(HTTP {code})") in out


def test_status_names_the_providers_own_api_key_env(capsys):
    with FakeStudioForge() as sf:
        sf.route("GET", "/v1/models", 401, {})
        out = _status(_cfg(sf.url + "/v1", api_key_env="MY_GATEWAY_KEY"), capsys)
    assert " UP " in _provider_line(out)
    assert "reachable, but it wants an API key: set $MY_GATEWAY_KEY (HTTP 401)" in out


def test_status_says_the_key_was_rejected_when_one_is_set(capsys, monkeypatch):
    monkeypatch.setenv("MY_GATEWAY_KEY", "wrong")
    with FakeStudioForge() as sf:
        sf.route("GET", "/v1/models", 401, {})
        out = _status(_cfg(sf.url + "/v1", api_key_env="MY_GATEWAY_KEY"), capsys)
        assert sf.requests[0]["headers"]["Authorization"] == "Bearer wrong"
    assert "rejected the API key from $MY_GATEWAY_KEY (HTTP 401)" in out
    assert "wants an API key" not in out


def test_status_is_silent_when_the_listing_is_served(capsys):
    with FakeStudioForge() as sf:
        sf.route("GET", "/v1/models", 200, {"data": [{"id": "m1"}]})
        out = _status(_cfg(sf.url + "/v1"), capsys)
    assert " UP " in _provider_line(out)
    assert "API key" not in out.replace("key: $", "")


def test_status_does_not_flag_a_plain_404(capsys):
    with FakeStudioForge() as sf:               # unrouted -> 404, still "alive" for openai
        out = _status(_cfg(sf.url + "/v1"), capsys)
    assert " UP " in _provider_line(out)
    assert "wants an API key" not in out and "rejected" not in out


def test_alive_still_counts_401_as_reachable():
    with FakeStudioForge() as sf:
        sf.route("GET", "/v1/models", 401, {})
        prov = providers.get_provider(_cfg(sf.url + "/v1"), "llamacpp")
        assert prov.alive() is True
        assert prov.auth_wall() == 401


def test_down_server_is_not_an_auth_wall(capsys):
    with FakeStudioForge() as sf:
        url = sf.url + "/v1"
    out = _status(_cfg(url), capsys)             # server closed -> refused
    assert " DOWN " in _provider_line(out)
    assert "wants an API key" not in out


@pytest.mark.parametrize("code", [401, 403])
def test_models_discover_says_it_wants_a_key(capsys, code):
    with FakeStudioForge() as sf:
        sf.route("GET", "/v1/models", code, {})
        cfg = _cfg(sf.url + "/v1")
        rc = cli.cmd_models(argparse.Namespace(action="discover", provider="llamacpp"), cfg)
    out = capsys.readouterr().out
    assert rc == 1
    assert "llamacpp: no model listing: reachable, but it wants an API key: set $STUDIOFORGE_API_KEY" in out
    assert f"(HTTP {code})" in out


def test_models_discover_unchanged_when_no_auth_wall(capsys):
    with FakeStudioForge() as sf:
        sf.route("GET", "/v1/models", 404, {})
        rc = cli.cmd_models(argparse.Namespace(action="discover", provider="llamacpp"),
                            _cfg(sf.url + "/v1"))
    assert rc == 1
    assert ("llamacpp: no model listing (endpoint has no /models or key missing)"
            in capsys.readouterr().out)


def test_models_discover_lists_models_when_key_accepted(capsys, monkeypatch):
    monkeypatch.setenv("MY_GATEWAY_KEY", "good")
    with FakeStudioForge() as sf:
        sf.route("GET", "/v1/models", 200, {"data": [{"id": "m1"}, {"id": "m2"}]})
        rc = cli.cmd_models(argparse.Namespace(action="discover", provider="llamacpp"),
                            _cfg(sf.url + "/v1", api_key_env="MY_GATEWAY_KEY"))
    out = capsys.readouterr().out
    assert rc == 0 and "2 models on llamacpp" in out
