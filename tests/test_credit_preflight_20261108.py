"""Hosted-provider credit preflight (2026-11-08): one request per provider,
a billing answer is a named early stop, never a key in a log."""
from __future__ import annotations

import logging

from crucibleforge import api, cli, preflight
from crucibleforge.providers import clear_cache


class _Resp:
    def __init__(self, status, body="", js=None):
        self.status_code = status
        self.text = body
        self._js = js

    def json(self):
        return self._js


def _install(monkeypatch, handler):
    calls = []

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            calls.append(("GET", url, kw))
            return handler("GET", url, kw)

        def post(self, url, **kw):
            calls.append(("POST", url, kw))
            return handler("POST", url, kw)

    monkeypatch.setattr(api.httpx, "Client", Client)
    clear_cache()
    return calls


def _cfg():
    return {"providers": {
        "deepseek": {"type": "openai", "base_url": "https://api.deepseek.com/v1",
                     "api_key": "SECRET-DS"},
        "other": {"type": "openai", "base_url": "https://api.other.example/v1",
                  "api_key": "SECRET-OT"}},
        "models": [
            {"name": "ds1", "provider": "deepseek", "model_id": "deepseek-v4-flash"},
            {"name": "ds2", "provider": "deepseek", "model_id": "deepseek-v4-pro"},
            {"name": "o1", "provider": "other", "model_id": "o-1"},
            {"name": "o2", "provider": "other", "model_id": "o-2"}]}


def test_deepseek_empty_balance_stops_its_models_with_one_request(monkeypatch, caplog):
    def h(method, url, kw):
        if "user/balance" in url:
            return _Resp(200, js={"is_available": False})
        return _Resp(200, "{}")
    calls = _install(monkeypatch, h)
    cfg = _cfg()
    with caplog.at_level(logging.DEBUG):
        bad = preflight.hosted_credit_problems(cfg, cfg["models"])
    assert set(bad) == {"ds1", "ds2"}
    assert "deepseek" in bad["ds1"] and "balance" in bad["ds1"]
    assert [c[0] for c in calls if "deepseek" in c[1]] == ["GET"]
    # one request per provider
    assert len(calls) == 2
    assert "SECRET" not in caplog.text and all("SECRET" not in v for v in bad.values())


def test_deepseek_with_balance_is_fine(monkeypatch):
    _install(monkeypatch, lambda m, u, k: _Resp(200, "{}", js={"is_available": True}))
    cfg = _cfg()
    assert preflight.hosted_credit_problems(cfg, cfg["models"][:2]) == {}


def test_a_402_or_spending_limit_body_is_an_early_stop(monkeypatch):
    def h(method, url, kw):
        if "other.example" in url:
            return _Resp(402, '{"error":"Token Plan usage limit has been reached"}')
        return _Resp(200, "{}", js={"is_available": True})
    _install(monkeypatch, h)
    cfg = _cfg()
    bad = preflight.hosted_credit_problems(cfg, cfg["models"])
    assert set(bad) == {"o1", "o2"}
    assert "other" in bad["o1"] and "402" in bad["o1"]


def test_a_429_with_a_spending_limit_body_counts_but_a_plain_429_does_not(monkeypatch):
    cfg = _cfg()
    _install(monkeypatch, lambda m, u, k: _Resp(429, "monthly spending limit reached"))
    assert set(preflight.hosted_credit_problems(cfg, cfg["models"][2:])) == {"o1", "o2"}
    _install(monkeypatch, lambda m, u, k: _Resp(429, "slow down"))
    assert preflight.hosted_credit_problems(cfg, cfg["models"][2:]) == {}


def test_a_bad_model_or_key_is_not_a_credit_problem(monkeypatch):
    cfg = _cfg()
    for status in (400, 401, 404, 500):
        _install(monkeypatch, lambda m, u, k, s=status: _Resp(s, "bad request"))
        assert preflight.hosted_credit_problems(cfg, cfg["models"][2:]) == {}


def test_a_failing_probe_never_blocks(monkeypatch):
    def h(*a):
        raise OSError("down")
    _install(monkeypatch, h)
    cfg = _cfg()
    assert preflight.hosted_credit_problems(cfg, cfg["models"]) == {}


def test_fail_fast_reports_the_provider_and_skips_conformance(monkeypatch, tmp_path):
    from crucibleforge import config
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    _install(monkeypatch, lambda m, u, k: _Resp(402, "payment required"))
    monkeypatch.setattr(preflight, "unrunnable_reason", lambda cfg, e: None)
    cfg = _cfg()
    ok, bad = cli._fail_fast(cfg, cfg["models"][2:], check_judge=False, judge=None)
    assert ok == []
    assert "other: out of credit" in bad["o1"]
