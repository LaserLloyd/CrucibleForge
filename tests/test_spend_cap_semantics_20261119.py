"""One spend-cap rule everywhere: `--spend-cap-usd` caps THIS invocation's own spend (it never reads the shared
ledger); `$CF_SPEND_CAP_USD` with a ledger is a cumulative budget across invocations; both may apply."""
import argparse
import json

import pytest

from crucibleforge import cli, spend


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for k in (spend.CAP_ENV, spend.RUN_CAP_ENV, spend.LEDGER_ENV, spend.CAP_MODEL_ENV, spend.GLOBAL_CAP_ENV,
              "CF_SPEND_JOURNAL", "PYTEST_CURRENT_TEST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(spend, "global_cap", lambda: None)
    spend.reset()
    yield
    spend.reset()


def ledger_with(tmp_path, usd):
    p = tmp_path / "led.jsonl"
    p.write_text(json.dumps({"model": "m", "cost_usd": usd}) + "\n", encoding="utf-8")
    return p


class R:
    prompt_tokens, completion_tokens = 1000, 1000


PRICE = {"input": 1.0, "output": 1.0}


def test_the_flag_ignores_earlier_spend_in_the_shared_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv(spend.LEDGER_ENV, str(ledger_with(tmp_path, 4.12)))
    cli._apply_process_switches(argparse.Namespace(spend_cap_usd=0.80))
    assert spend.cap() is None and spend.run_cap() == 0.80
    tok = spend.reserve("openai", "m", 0.01)          # not refused by the 4.12 already in the ledger
    spend.release(tok)


def test_the_flag_stops_this_runs_own_spend(monkeypatch, tmp_path):
    monkeypatch.setenv(spend.LEDGER_ENV, str(ledger_with(tmp_path, 4.12)))
    monkeypatch.setenv(spend.RUN_CAP_ENV, "0.05")
    spend.release(spend.reserve("openai", "m", 0.01))
    spend.record("openai", "m", PRICE, R)               # 0.002
    for _ in range(30):
        spend.record("openai", "m", PRICE, R)
    with pytest.raises(spend.SpendCapExceeded) as e:
        spend.reserve("openai", "m", 0.01)
    assert e.value.cap_name == "run cap"
    with pytest.raises(spend.SpendCapExceeded):
        spend.guard("openai", "m")


def test_the_env_cap_with_a_ledger_is_cumulative(monkeypatch, tmp_path):
    monkeypatch.setenv(spend.LEDGER_ENV, str(ledger_with(tmp_path, 4.12)))
    monkeypatch.setenv(spend.CAP_ENV, "4.00")
    with pytest.raises(spend.SpendCapExceeded) as e:
        spend.reserve("openai", "m", 0.01)
    assert e.value.cap_name == "cap"


def test_both_caps_apply_and_local_models_are_never_refused(monkeypatch, tmp_path):
    monkeypatch.setenv(spend.LEDGER_ENV, str(ledger_with(tmp_path, 1.0)))
    monkeypatch.setenv(spend.CAP_ENV, "5")
    monkeypatch.setenv(spend.RUN_CAP_ENV, "0.001")
    with pytest.raises(spend.SpendCapExceeded):          # run cap bites first
        spend.reserve("openai", "m", 0.01)
    assert not spend.reserve("studioforge", "m", 0.01)   # rig: free
    monkeypatch.setenv(spend.RUN_CAP_ENV, "50")
    monkeypatch.setenv(spend.CAP_ENV, "1.0")
    with pytest.raises(spend.SpendCapExceeded):          # budget bites
        spend.reserve("openai", "m", 0.01)


def test_the_cli_flag_sets_the_run_cap_not_the_env_cap(monkeypatch):
    src = open(cli.__file__, encoding="utf-8").read()
    assert "os.environ[spend.RUN_CAP_ENV] = repr(float(cap))" in src
    assert "os.environ[spend.CAP_ENV] = repr(float(cap))" not in src
    assert "CF_SPEND_RUN_CAP_USD" in cli.SPEND_ENV


def test_the_plan_line_shows_both_figures(monkeypatch, tmp_path):
    assert spend.budget_line() is None
    monkeypatch.setenv(spend.LEDGER_ENV, str(ledger_with(tmp_path, 4.12)))
    monkeypatch.setenv(spend.RUN_CAP_ENV, "0.8")
    monkeypatch.setenv(spend.CAP_ENV, "10")
    assert spend.budget_line() == "  spend caps - this run: up to $0.80; budget: $4.12 spent of $10.00"


def test_agree_counts_its_own_calls_and_the_budget_still_applies(monkeypatch, tmp_path):
    from crucibleforge import agree as AG
    monkeypatch.setenv(spend.LEDGER_ENV, str(ledger_with(tmp_path, 4.12)))
    monkeypatch.setenv(spend.CAP_ENV, "4.00")           # the cumulative budget is already over
    with pytest.raises(spend.SpendCapExceeded):
        spend.reserve("openai", "m", 0.01)               # agree's provider path hits this too
    c = AG.CappedClient(object(), PRICE, 0.80)
    assert c.spent == 0.0                                # its --cap-usd ignores the ledger's 4.12
