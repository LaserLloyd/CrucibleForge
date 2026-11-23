"""Fail closed: a hosted model with no price entry never bypasses the spend caps."""
import pytest

from crucibleforge import profiles, providers, spend


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for k in (spend.CAP_ENV, spend.LEDGER_ENV, spend.CAP_MODEL_ENV, spend.GLOBAL_CAP_ENV,
              spend.BASELINE_ENV, spend.HELPER_JOURNAL_ENV, spend.RERUN_DIR_ENV,
              "CF_SPEND_JOURNAL", "PYTEST_CURRENT_TEST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(spend, "BENCH45_LEDGER", tmp_path / "bench45.jsonl")
    monkeypatch.setattr(spend, "DEFAULT_HELPER_JOURNAL", tmp_path / "no-helper.jsonl")
    monkeypatch.setattr(spend, "DEFAULT_RERUN_DIR", tmp_path / "no-rerun")
    monkeypatch.setattr(providers, "ALLOW_UNPRICED", False)
    spend.reset()
    yield
    spend.reset()


def _prov(monkeypatch, ptype, prices=None, profile="bench45"):
    monkeypatch.setattr(profiles, "active_name", lambda cfg=None: profile)
    sent = []

    def fake(base_url, key, model, messages, **kw):
        sent.append(model)
        return type("R", (), {"content": "ok", "usage": {"prompt_tokens": 1, "completion_tokens": 1}})()
    import crucibleforge.api as api
    monkeypatch.setattr(api, "stream_chat_retried", fake)
    p = providers.Provider(name="p", type=ptype, base_url="http://x/v1", api_key="k")
    p.prices = dict(prices or {})
    return p, sent


def test_unpriced_hosted_under_bench45_is_refused(monkeypatch):
    # fix round 2026-11-18 (P4): bench45's standing default cap alone no longer arms this gate (it made
    # every unpriced fake/probe fail); a cap set on purpose does. The run entry refuses a real unpriced
    # run before any call (tests/test_spend_gate_default_profile_20261118.py).
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "330")
    p, sent = _prov(monkeypatch, "openai")
    with pytest.raises(spend.SpendCapExceeded) as e:
        p.chat("paid-model", [{"role": "user", "content": "hi"}])
    assert "paid-model" in str(e.value) and "--allow-unpriced" in str(e.value) and "price" in str(e.value)
    assert sent == []


def test_unpriced_hosted_allowed_with_flag(monkeypatch):
    p, sent = _prov(monkeypatch, "openai")
    monkeypatch.setattr(providers, "ALLOW_UNPRICED", True)
    p.chat("paid-model", [{"role": "user", "content": "hi"}])
    assert sent == ["paid-model"]


def test_unpriced_rig_model_is_allowed(monkeypatch):
    for t in ("studioforge", "lmstudio"):
        p, sent = _prov(monkeypatch, t)
        p.chat("rig-model", [{"role": "user", "content": "hi"}])
        assert sent == ["rig-model"]


def test_priced_hosted_is_unchanged(monkeypatch):
    p, sent = _prov(monkeypatch, "openai", prices={"m": {"input": 1.0, "output": 2.0}})
    p.chat("m", [{"role": "user", "content": "hi"}])
    assert sent == ["m"]


def test_bench_profile_is_unchanged(monkeypatch):
    p, sent = _prov(monkeypatch, "openai", profile="bench")
    p.chat("paid-model", [{"role": "user", "content": "hi"}])
    assert sent == ["paid-model"]


def test_any_spend_cap_also_closes_the_gate(monkeypatch):
    p, sent = _prov(monkeypatch, "openai", profile="bench")
    monkeypatch.setenv(spend.CAP_ENV, "5")
    with pytest.raises(spend.SpendCapExceeded):
        p.chat("paid-model", [{"role": "user", "content": "hi"}])
    assert sent == []


def test_cmd_run_flag_sets_the_allowance():
    import inspect
    from crucibleforge import cli
    assert "providers.ALLOW_UNPRICED" in inspect.getsource(cli.cmd_run)
