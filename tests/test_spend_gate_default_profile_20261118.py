"""Spend gate (fix round 2026-11-18, P4): bench45 being the default profile must not arm a cap that refuses
unpriced hosted calls in tests, dry plans and conformance probes; an explicit cap still blocks exactly as before;
a real run of an unpriced hosted model is refused before its first call with a message naming the missing price."""
import argparse
import json

import pytest

from crucibleforge import cli, profiles, providers, spend


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for k in (spend.CAP_ENV, spend.LEDGER_ENV, spend.CAP_MODEL_ENV, spend.GLOBAL_CAP_ENV,
              spend.BASELINE_ENV, spend.HELPER_JOURNAL_ENV, spend.RERUN_DIR_ENV,
              "CF_SPEND_JOURNAL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(spend, "BENCH45_LEDGER", tmp_path / "bench45.jsonl")
    monkeypatch.setattr(spend, "DEFAULT_HELPER_JOURNAL", tmp_path / "no-helper.jsonl")
    monkeypatch.setattr(spend, "DEFAULT_RERUN_DIR", tmp_path / "no-rerun")
    monkeypatch.setattr(providers, "ALLOW_UNPRICED", False)
    monkeypatch.setattr(profiles, "active_name", lambda cfg=None: "bench45")
    spend.reset()
    yield
    spend.reset()


def _prov(monkeypatch, prices=None):
    sent = []

    def fake(base_url, key, model, messages, **kw):
        sent.append(model)
        return type("R", (), {"content": "ok", "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
                              "prompt_tokens": 1000, "completion_tokens": 1000,
                              "finish_reason": "stop"})()
    import crucibleforge.api as api
    monkeypatch.setattr(api, "stream_chat_retried", fake)
    p = providers.Provider(name="p", type="openai", base_url="http://x/v1", api_key="k")
    p.prices = dict(prices or {})
    return p, sent


def test_the_default_profile_alone_does_not_refuse_an_unpriced_hosted_call(monkeypatch):
    assert spend.global_cap() is not None          # bench45 still has its standing global cap ...
    assert spend.explicit_cap_set("paid-model") is False   # ... but nobody set a cap explicitly
    p, sent = _prov(monkeypatch)
    p.chat("paid-model", [{"role": "user", "content": "hi"}])
    assert sent == ["paid-model"]


@pytest.mark.parametrize("env", [spend.CAP_ENV, spend.GLOBAL_CAP_ENV])
def test_an_explicit_cap_still_refuses_an_unpriced_hosted_call(monkeypatch, env):
    monkeypatch.setenv(env, "15")
    assert spend.explicit_cap_set("paid-model") is True
    p, sent = _prov(monkeypatch)
    with pytest.raises(spend.SpendCapExceeded, match="no price"):
        p.chat("paid-model", [{"role": "user", "content": "hi"}])
    assert sent == []


def test_a_fifteen_dollar_cap_stops_a_priced_run_and_journals_it(monkeypatch, tmp_path):
    """The 2026-11-18 incident: a $15 cap stopped a frontier run with HTTP 402 "spend cap reached"."""
    journal = tmp_path / "j.jsonl"
    monkeypatch.setenv("CF_SPEND_JOURNAL", str(journal))
    monkeypatch.setenv(spend.CAP_ENV, "15")
    # $5 per call: 1000 in at $1000/M + 1000 out at $4000/M
    p, sent = _prov(monkeypatch, prices={"m": {"input": 1000.0, "output": 4000.0}})
    with pytest.raises(spend.SpendCapExceeded) as e:
        for _ in range(10):
            p.chat("m", [{"role": "user", "content": "hi"}], max_tokens=10)
    assert e.value.status == 402 and "spend cap reached" in str(e.value)
    assert 1 <= len(sent) <= 3
    lines = [json.loads(x) for x in journal.read_text(encoding="utf-8").splitlines()]
    paid = [x for x in lines if "call_usd" in x]
    assert paid and all("total_usd" in x for x in paid)
    assert any(x.get("refused") is True and "total_usd" in x for x in lines)


def _run_args(**kw):
    base = dict(cmd="run", models="h1", categories=None, difficulty=None, cases=None, fresh=False, force=False,
                smoke=False, yes=True, no_link_check=True, judge=None, force_evict=False, profile=None,
                skip_conformance=True, allow_unpriced=False, spend_cap_usd=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _cfg():
    return {"providers": {"api": {"type": "openai", "base_url": "http://x/v1", "api_key": "k"}},
            "models": [{"name": "h1", "provider": "api", "model_id": "h1-id", "context_length": 32768}],
            "defaults": {}, "judge": {"candidates": []}}


def _stub(monkeypatch):
    monkeypatch.setattr(cli, "_refuse_fresh_with_filter", lambda a: None)
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda a, c: (c, [{"id": "x", "category": "coding"}], {}))


def test_a_real_run_of_an_unpriced_hosted_model_is_refused_before_any_call(monkeypatch):
    _stub(monkeypatch)
    called = []
    monkeypatch.setattr(cli, "_fail_fast", lambda *a, **k: called.append(1) or ([], {}))
    with pytest.raises(SystemExit) as e:
        cli.cmd_run(_run_args(), _cfg())
    msg = str(e.value)
    assert "h1" in msg and "without a price" in msg and "price: {input" in msg
    assert "402" not in msg and called == []


def test_allow_unpriced_with_an_explicit_cap_is_refused_before_any_call(monkeypatch):
    """--allow-unpriced plus a cap would die on the first call with a 402-looking refusal: refuse up front."""
    monkeypatch.setenv(spend.CAP_ENV, "15")
    _stub(monkeypatch)
    called = []
    monkeypatch.setattr(cli, "_fail_fast", lambda *a, **k: called.append(1) or ([], {}))
    with pytest.raises(SystemExit) as e:
        cli.cmd_run(_run_args(allow_unpriced=True), _cfg())
    assert "h1" in str(e.value) and "no price" in str(e.value) and "cap" in str(e.value)
    assert called == []


# ---------------------------------------------------------------- the plan price (D15)

def _plan_cfg(price=None):
    api = {"name": "api", "model_id": "M", "provider": "hosted", "context_length": 1000000}
    if price is not None:
        api["price"] = price
    return {"models": [{"name": "rig", "model_id": "a/b", "provider": "studioforge", "context_length": 32768}, api],
            "providers": {"studioforge": {"type": "studioforge", "base_url": "http://s.invalid/v1"},
                          "hosted": {"type": "openai", "base_url": "http://m.invalid/v1"}},
            "defaults": {"context_length": 32768}, "judge": {"candidates": [], "samples": 3}}


def _plan(capsys, cfg, models):
    args = cli.argparse.Namespace(models=models, profile=None, judge=None, smoke=False, plan=True)
    assert cli.cmd_all(args, cfg) == 0
    return capsys.readouterr().out


def test_the_plan_prints_a_price_per_hosted_model(capsys):
    out = _plan(capsys, _plan_cfg({"input": 1.0, "output": 2.0}), "api")
    m = __import__("re").search(r"price \(estimate[^)]*\): ~\$([\d.]+) expected .*?\$([\d.]+) if every row", out)
    assert m, out
    exp, ceil = float(m.group(1)), float(m.group(2))
    assert 0 < exp <= ceil


def test_the_plan_says_when_a_hosted_model_has_no_price(capsys):
    out = _plan(capsys, _plan_cfg(), "api")
    assert "price: NOT SET" in out and "a real run is refused" in out


def test_the_plan_says_a_rig_model_has_no_bill(capsys):
    assert "price: rig model, no bill" in _plan(capsys, _plan_cfg(), "rig")


def test_the_estimate_scales_with_the_price():
    from crucibleforge import plan_price
    rows = [({"id": "x", "prompt": "a" * 300, "category": "coding"}, 2, 1000)]
    one = plan_price.estimate(rows, {"input": 1.0, "output": 1.0}, thinks=False)
    two = plan_price.estimate(rows, {"input": 2.0, "output": 2.0}, thinks=False)
    # 4.5.1: expected tokens = the measured per-run profile x the share of the standard run's rows planned
    share = 2 / plan_price.MEASURED["ref_rows"]
    assert one["rows"] == 2 and one["in_tok"] == pytest.approx(plan_price.MEASURED["typical"][0] * 1e6 * share)
    assert one["ceiling_tok"] == 2000                                     # the ceiling is still each row's budget
    assert two["expected_usd"] == pytest.approx(2 * one["expected_usd"])
    assert plan_price.estimate(rows, None, thinks=False)["expected_usd"] is None
