"""bench45 spend ledger: fixed path, per-model cap scope, global gate, `spend status`."""
import json
import subprocess
import sys

import pytest

from crucibleforge import profiles, spend


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for k in (spend.CAP_ENV, spend.LEDGER_ENV, spend.CAP_MODEL_ENV, spend.GLOBAL_CAP_ENV,
              spend.BASELINE_ENV, spend.HELPER_JOURNAL_ENV, spend.RERUN_DIR_ENV, spend.COUNT_PLAN_ENV,
              "CF_SPEND_JOURNAL", "PYTEST_CURRENT_TEST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(spend, "BENCH45_LEDGER", tmp_path / "fixed" / "bench45.jsonl")
    monkeypatch.setattr(spend, "DEFAULT_HELPER_JOURNAL", tmp_path / "no-helper.jsonl")   # never the real ones
    monkeypatch.setattr(spend, "DEFAULT_RERUN_DIR", tmp_path / "no-rerun")
    spend.reset()
    yield
    spend.reset()


def put(path, *lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for d in lines:
            f.write(json.dumps(d) + "\n")


def test_fixed_path_only_for_bench45(monkeypatch):
    # under pytest (PYTEST_CURRENT_TEST is set by the runner) there is never a default
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    monkeypatch.setattr(profiles, "active_name", lambda cfg=None: "bench45")
    assert spend.ledger_path() is None
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert spend.ledger_path() == spend.BENCH45_LEDGER
    monkeypatch.setattr(profiles, "active_name", lambda cfg=None: "bench")
    assert spend.ledger_path() is None                       # 4.1 is unchanged
    monkeypatch.setattr(profiles, "active_name", lambda cfg=None: "bench45")
    monkeypatch.setenv(spend.LEDGER_ENV, "")
    assert spend.ledger_path() is None                       # an explicit empty value turns it off
    monkeypatch.setenv(spend.LEDGER_ENV, "/x/y.jsonl")
    assert str(spend.ledger_path()) == "/x/y.jsonl"          # env wins


def test_calls_book_to_the_fixed_ledger(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(profiles, "active_name", lambda cfg=None: "bench45")
    monkeypatch.setenv(spend.CAP_ENV, "100")
    spend._add("claude-haiku-5-5", 1.25)
    spend._add("claude-fable-5-2", 2.0)
    lines = [json.loads(x) for x in spend.BENCH45_LEDGER.read_text(encoding="utf-8").splitlines()]
    assert [round(x["cost_usd"], 2) for x in lines] == [1.25, 2.0]
    assert round(spend.spent(), 2) == 3.25


def test_per_model_cap_counts_only_that_model(monkeypatch, tmp_path):
    led = tmp_path / "l.jsonl"
    put(led, {"model": "fable", "cost_usd": 14.99}, {"model": "haiku", "cost_usd": 40.0})
    monkeypatch.setenv(spend.LEDGER_ENV, str(led))
    monkeypatch.setenv(spend.CAP_ENV, "15")
    monkeypatch.setenv(spend.CAP_MODEL_ENV, "haiku")
    with pytest.raises(spend.SpendCapExceeded):               # haiku alone is at 40 > 15
        spend.guard("anthropic", "haiku")
    monkeypatch.setenv(spend.CAP_MODEL_ENV, "fable")
    spend.guard("anthropic", "fable")                         # 14.99 < 15, others do not count
    tok = spend.reserve("anthropic", "fable", 0.005)
    spend.release(tok)
    with pytest.raises(spend.SpendCapExceeded):
        spend.reserve("anthropic", "fable", 0.02)             # incoming call crosses the cap


def test_global_gate_counts_outside_journals(monkeypatch, tmp_path):
    led = tmp_path / "l.jsonl"
    helper = tmp_path / "h.jsonl"
    put(led, {"model": "fable", "cost_usd": 10.0})
    put(helper, {"model": "deepseek-v4-flash", "cost": 5.0}, {"model": "grok-4.7", "cost": 99.0})
    monkeypatch.setenv(spend.LEDGER_ENV, str(led))
    monkeypatch.setenv(spend.HELPER_JOURNAL_ENV, str(helper))
    monkeypatch.setenv(spend.BASELINE_ENV, "100")
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "116")           # 100 + 10 + 5 = 115 (Grok is a plan model)
    spend.guard("anthropic", "fable")                         # 115 < 116, no per-run cap needed
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "115")
    with pytest.raises(spend.SpendCapExceeded) as e:
        spend.guard("anthropic", "fable")
    assert "global" in str(e.value)
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "116")
    with pytest.raises(spend.SpendCapExceeded):
        spend.reserve("anthropic", "fable", 1.5)              # the incoming call counts too
    spend.guard("studioforge", "local")                       # local models are never refused


def test_journal_shapes_and_plan_split(tmp_path):
    p = tmp_path / "j.jsonl"
    put(p, {"model": "claude-x", "call_usd": 1.5}, {"model": "claude-x", "cost_usd": 0.5},
        {"model": "deepseek-v4-flash", "cost": 0.25}, {"model": "grok-4.7", "cost": 4.0},
        {"model": "claude-x", "refused": True, "call_usd": 9}, {"kind": "reserve", "usd": 7, "model": "a"})
    p.write_text(p.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    s = spend.sum_journal(p)
    assert s["total"] == 6.25 and s["plan"] == 4.0 and s["metered"] == 2.25
    assert spend.sum_journal(tmp_path / "missing.jsonl")["total"] == 0.0


def test_status_adds_every_source_against_the_cap(tmp_path):
    led, helper, rr = tmp_path / "l.jsonl", tmp_path / "h.jsonl", tmp_path / "rr"
    put(led, {"model": "fable", "cost_usd": 3.0})
    put(helper, {"model": "deepseek", "cost": 1.0}, {"model": "grok-4.7", "cost": 3.92})
    put(rr / "a.jsonl", {"model": "opus", "call_usd": 5.71})
    put(rr / "b.jsonl", {"model": "fable", "call_usd": 1.95}, {"model": "x", "refused": True})
    st = spend.status(ledger=led, helper=helper, rerun_dir=rr, baseline=231.07, cap_usd=330)
    assert st["total_usd"] == round(231.07 + 3 + 1 + 5.71 + 1.95, 4)
    assert st["plan_usd_not_counted"] == 3.92 and not st["over"]
    assert st["headroom_usd"] == round(330 - st["total_usd"], 4)
    st2 = spend.status(ledger=led, helper=helper, rerun_dir=rr, baseline=231.07, cap_usd=330, count_plan=True)
    assert st2["total_usd"] == round(st["total_usd"] + 3.92, 4)
    assert spend.status(ledger=led, baseline=331, cap_usd=330)["over"] is True
    assert "HEADROOM" in spend.render_status(st)


def test_cli_spend_status_exit_code(tmp_path):
    led = tmp_path / "l.jsonl"
    put(led, {"model": "fable", "cost_usd": 3.0})
    # explicit empty helper journal / rerun dir: under bench45 (the default since the release flip) a bare
    # `spend status` also adds the live helper journal and rerun ledgers, which this test must not read
    base = [sys.executable, "-m", "crucibleforge.cli", "spend", "status", "--ledger", str(led),
            "--helper-journal", str(tmp_path / "none.jsonl"), "--rerun-dir", str(tmp_path / "none")]
    r = subprocess.run([*base, "--baseline", "10", "--cap", "330", "--json"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["total_usd"] == 13.0
    r = subprocess.run([*base, "--baseline", "330", "--cap", "330"], capture_output=True, text=True)
    assert r.returncode == 1 and "OVER THE CAP" in r.stdout


def test_global_cap_is_the_default_under_bench45(monkeypatch, tmp_path):
    """No env var: bench45 alone enforces the $330 global cap (and not 4.1)."""
    led = tmp_path / "l.jsonl"
    put(led, {"model": "fable", "cost_usd": 98.9})           # 231.07 baseline + 98.9 = 329.97
    monkeypatch.setenv(spend.LEDGER_ENV, str(led))
    monkeypatch.setattr(profiles, "active_name", lambda cfg=None: "bench45")
    assert spend.global_cap() == 330.0
    spend.guard("anthropic", "fable")                         # 329.97 < 330
    with pytest.raises(spend.SpendCapExceeded, match="global"):
        spend.reserve("anthropic", "fable", 0.05)             # the incoming call crosses 330
    put(led, {"model": "fable", "cost_usd": 0.05})
    with pytest.raises(spend.SpendCapExceeded):
        spend.guard("anthropic", "fable")
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "400")           # env is an override only
    spend.guard("anthropic", "fable")
    monkeypatch.delenv(spend.GLOBAL_CAP_ENV)
    monkeypatch.setattr(profiles, "active_name", lambda cfg=None: "bench")
    assert spend.global_cap() is None
    spend.guard("anthropic", "fable")                         # 4.1: never gated


def test_helper_and_rerun_journals_default_under_bench45(monkeypatch, tmp_path):
    h, rr = tmp_path / "spend.jsonl", tmp_path / "rr"
    put(h, {"model": "deepseek", "cost": 1.0})
    put(rr / "a.jsonl", {"model": "opus", "call_usd": 5.0})
    monkeypatch.setattr(spend, "DEFAULT_HELPER_JOURNAL", h)
    monkeypatch.setattr(spend, "DEFAULT_RERUN_DIR", rr)
    monkeypatch.setattr(profiles, "active_name", lambda cfg=None: "bench45")
    st = spend.status(ledger=tmp_path / "none.jsonl")
    assert st["total_usd"] == 231.07 + 1.0 + 5.0               # baseline default + both journals
    monkeypatch.setenv(spend.HELPER_JOURNAL_ENV, str(tmp_path / "other.jsonl"))
    assert spend.status(ledger=tmp_path / "none.jsonl")["total_usd"] == 231.07 + 5.0   # env overrides
    monkeypatch.setattr(profiles, "active_name", lambda cfg=None: "bench")
    assert spend.status(ledger=tmp_path / "none.jsonl")["total_usd"] == 0.0           # 4.1: no defaults


def test_missing_default_paths_are_a_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(spend, "DEFAULT_HELPER_JOURNAL", tmp_path / "nope.jsonl")
    monkeypatch.setattr(spend, "DEFAULT_RERUN_DIR", tmp_path / "nodir")
    monkeypatch.setattr(profiles, "active_name", lambda cfg=None: "bench45")
    st = spend.status(ledger=tmp_path / "none.jsonl")
    assert st["total_usd"] == 231.07 and set(st["parts"]) == {"baseline", "bench_ledger"}


# ------------------------------------------------------------------ S10 review fixes
def test_global_cap_is_enforced_without_a_ledger(monkeypatch, tmp_path):
    """Review 1: CF_SPEND_LEDGER= (no ledger) or a global cap off-profile must not turn the
    global gate off: baseline + helper + reruns + this process's own spend still count."""
    helper = tmp_path / "h.jsonl"
    put(helper, {"model": "deepseek-v4-flash", "cost": 5.0})
    monkeypatch.setenv(spend.LEDGER_ENV, "")                  # explicitly no ledger
    monkeypatch.setenv(spend.HELPER_JOURNAL_ENV, str(helper))
    monkeypatch.setenv(spend.BASELINE_ENV, "100")
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "106")           # 100 + 5 = 105 booked outside
    assert spend.ledger_path() is None
    spend.guard("anthropic", "fable")
    with pytest.raises(spend.SpendCapExceeded, match="global"):
        spend.reserve("anthropic", "fable", 1.5)              # 105 + 1.5 > 106
    tok = spend.reserve("anthropic", "fable", 0.5)
    spend.release(tok)
    spend._add("fable", 1.0)                                  # this process booked $1 -> 106
    with pytest.raises(spend.SpendCapExceeded, match="global"):
        spend.guard("anthropic", "fable")
    spend.guard("studioforge", "local")                       # local is never refused


def test_unpriced_call_is_charged_when_only_the_global_cap_is_on(monkeypatch, tmp_path):
    """Review 2: with no per-run cap but a global cap, a hosted reply with no usage is charged
    at its estimate (never booked as free)."""
    led = tmp_path / "l.jsonl"
    monkeypatch.setenv(spend.LEDGER_ENV, str(led))
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "330")
    assert spend.cap() is None

    class R:
        prompt_tokens = completion_tokens = None
    assert spend.record("anthropic", "fable", {"input": 1, "output": 5}, R(), estimate=0.25) == 0.25
    assert spend.record("anthropic", "fable", None, R()) == spend.FALLBACK_ESTIMATE_USD
    lines = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines()]
    assert [round(x["cost_usd"], 2) for x in lines] == [0.25, 0.05]


def test_per_model_cap_applies_to_that_model_only(monkeypatch, tmp_path):
    """Review 3: CF_SPEND_CAP_MODEL names the model the per-run cap belongs to; another model's
    call (a second model in the run, a hosted judge) is not measured against it (the global
    cap still is)."""
    led = tmp_path / "l.jsonl"
    put(led, {"model": "fable", "cost_usd": 15.0}, {"model": "haiku", "cost_usd": 1.0})
    monkeypatch.setenv(spend.LEDGER_ENV, str(led))
    monkeypatch.setenv(spend.CAP_ENV, "15")
    monkeypatch.setenv(spend.CAP_MODEL_ENV, "fable")
    with pytest.raises(spend.SpendCapExceeded):
        spend.guard("anthropic", "fable")                     # fable is at its cap
    spend.guard("anthropic", "haiku")                         # haiku is not fable's cap
    spend.release(spend.reserve("anthropic", "haiku", 3.0))
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "16.5")          # 16 booked: the global gate still holds
    with pytest.raises(spend.SpendCapExceeded, match="global"):
        spend.reserve("anthropic", "haiku", 1.0)


def test_a_bad_baseline_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(spend.LEDGER_ENV, str(tmp_path / "l.jsonl"))
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "330")
    monkeypatch.setenv(spend.BASELINE_ENV, "231,07")         # a typo, not a number
    with pytest.raises(spend.SpendCapExceeded, match="global"):
        spend.guard("anthropic", "fable")


def test_guard_does_not_count_the_callers_own_reservation_globally(monkeypatch, tmp_path):
    """Review 8: a call that reserved within the global cap is not refused at pre_send by its
    own reservation."""
    monkeypatch.setenv(spend.LEDGER_ENV, str(tmp_path / "l.jsonl"))
    monkeypatch.setenv(spend.BASELINE_ENV, "100")
    monkeypatch.setenv(spend.GLOBAL_CAP_ENV, "101")
    tok = spend.reserve("anthropic", "fable", 1.0)            # 100 + 1.0 = 101: admitted
    spend.guard("anthropic", "fable", own=tok)                # its own 1.0 is not counted again
    spend.release(tok)
