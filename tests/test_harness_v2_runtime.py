"""Harness v2 runtime fixes: the spend cap inside the API client, errored
pack rows read as pending (review B2), unrun-pack markers (B3), `pack
verify` timing with the set budget (B4) and per-row judge samples."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from crucibleforge import api, estimate, judge, ladder, providers, report, spend
from crucibleforge.profiles import load_profile


@pytest.fixture(autouse=True)
def _restore_process_env():
    """_apply_process_switches writes os.environ directly: put the staged and
    cap switches back so no later test sees them."""
    import os
    from crucibleforge import ladder as _l, spend as _s
    keep = {k: os.environ.get(k) for k in (_l.STAGED_ENV, _s.CAP_ENV)}
    yield
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _s.reset()


# ---------------------------------------------------------------- spend cap

@pytest.fixture
def capenv(monkeypatch):
    monkeypatch.delenv(spend.CAP_ENV, raising=False)
    monkeypatch.delenv(spend.LEDGER_ENV, raising=False)
    spend.reset()
    yield monkeypatch
    spend.reset()


def _res(pt, ct):
    return SimpleNamespace(prompt_tokens=pt, completion_tokens=ct)


def test_no_cap_never_refuses(capenv):
    spend.guard("openai", "m")
    assert spend.record("openai", "m", {"input": 1, "output": 1}, _res(10**6, 0)) == 1.0


def test_cap_stops_hosted_calls_before_sending(capenv):
    capenv.setenv(spend.CAP_ENV, "2")
    price = {"input": 1.0, "output": 2.0}
    spend.guard("openai", "m")
    assert spend.record("openai", "m", price, _res(10**6, 5 * 10**5)) == pytest.approx(2.0)
    with pytest.raises(spend.SpendCapExceeded):
        spend.guard("openai", "m")
    assert issubclass(spend.SpendCapExceeded, api.WindowClosed)     # job stays pending
    spend.guard("studioforge", "local-model")                        # local is free


def test_bad_cap_value_fails_closed(capenv):
    capenv.setenv(spend.CAP_ENV, "five")
    with pytest.raises(spend.SpendCapExceeded):
        spend.guard("openai", "m")


def test_shared_ledger_counts_every_process(capenv, tmp_path):
    led = tmp_path / "calls.jsonl"
    led.write_text(json.dumps({"cost_usd": 4.5}) + "\n", encoding="utf-8")
    capenv.setenv(spend.LEDGER_ENV, str(led))
    capenv.setenv(spend.CAP_ENV, "5")
    spend.guard("openai", "m")
    spend.record("openai", "m", {"input": 1, "output": 0}, _res(600_000, 0))
    assert spend.spent() == pytest.approx(5.1)
    with pytest.raises(spend.SpendCapExceeded):
        spend.guard("openai", "m")
    assert len(led.read_text(encoding="utf-8").splitlines()) == 2


def test_provider_chat_refuses_without_sending(capenv, monkeypatch):
    capenv.setenv(spend.CAP_ENV, "0")
    sent = []
    monkeypatch.setattr(api, "stream_chat_retried",
                        lambda *a, **k: sent.append(1) or _res(1, 1))
    # 4.5 (merge r2): only a PRICED model is capped; an unpriced one is never refused
    p = providers.Provider(name="x", type="openai", base_url="http://127.0.0.1:9/v1",
                           prices={"m": {"input": 1.0, "output": 1.0}})
    with pytest.raises(spend.SpendCapExceeded):
        p.chat("m", [{"role": "user", "content": "hi"}])
    assert sent == []


def test_cli_flag_sets_the_cap(capenv):
    import argparse

    from crucibleforge import cli
    cli._apply_process_switches(argparse.Namespace(include_staged=False, spend_cap_usd=3.0))
    assert spend.cap() == 3.0


# ------------------------------------------------- errored packs pending (B2)

def test_set_pending_jobs_reads_errors_and_gaps():
    spec = {"cases": ["A", "B", "C"]}
    rows = [{"case_id": "A", "repeat": 1, "grade": "pass"},
            {"case_id": "B", "repeat": 1, "grade": "error", "error_kind": "transport",
             "error": "HTTP 402 Insufficient Balance"}]
    got = report.set_pending_jobs(rows, spec)
    assert got == {"errored": 1, "missing": ["C"], "exhausted": 0}
    rows[1]["error_kind"] = "template"           # a permanent error is a result
    assert report.set_pending_jobs(rows, spec)["errored"] == 0


def test_pending_set_is_due_again():
    seq = [{"n": 1, "level": 1, "cases": ["A"]}]
    # an errored set has share None (points_scorecard), so it stays due
    assert ladder.due(0.95, {1: None}, seq, 0.9) == [1]
    assert ladder.states(0.95, {1: None}, seq, 0.9) == {1: "pending"}


def test_unrun_tags():
    assert report._unrun_tag(0) == ""
    assert report._unrun_tag(1) == " [+1 pack unrun]"
    assert report._unrun_tag(3) == " [+3 packs unrun]"


# ------------------------------------------------------- pack verify (B4)

def test_verify_timing_applies_the_set_budget(monkeypatch):
    seen = []

    def fake_minutes(prof, cases, budget=None, context=None):
        seen.append([c.get("ladder_budget") for c in cases])
        return 1.0
    monkeypatch.setattr(estimate, "pack_minutes", fake_minutes)
    spec = {"budget": 65536, "cases": [{"id": "Q", "category": "nsfw"}]}
    ladder._time_warnings(spec, spec["cases"], {"Q"}, {}, load_profile("bench", {}))
    assert seen == [[65536]]


# --------------------------------------------------------- judge samples

def test_row_samples_override(monkeypatch):
    pc = dict(judge.JUDGE_POLICY_DEFAULTS, row_samples={"LUX1-bone-choir": 3})
    row = {"case_id": "LUX1-bone-choir", "rubric": "story", "judge_elements": []}
    pol = judge.judge_policy(row, pc)
    assert pol["samples"] == 3 and pol["kind"] == "override"
    row2 = {"case_id": "X", "rubric": "story", "judge_elements": [], "judge_samples": 2}
    assert judge.judge_policy(row2, pc)["samples"] == 2


def test_judge_samples_is_a_grading_key():
    from crucibleforge import version
    assert "judge_samples" in version._GRADING_KEYS
    assert "content_rules_allow" in version._GRADING_KEYS


def test_exhausted_errored_job_is_a_terminal_errored_state_not_a_zero():
    """Fix pass M1 (revised): spent retries are infrastructure, not model quality:
    the set is 'errored' (no score, not pending forever, blocks the next level
    and MAXED), not a failed 0 and not 'unrun'."""
    from crucibleforge.bench_all import MAX_CASE_ATTEMPTS
    spec = {"cases": ["A", "B"]}
    rows = [{"case_id": "A", "repeat": 1, "grade": "pass"},
            {"case_id": "B", "repeat": 1, "grade": "error", "error_kind": "generation"}]
    assert report.set_pending_jobs(rows, spec, {"B": 1}) == {"errored": 1, "missing": [],
                                                              "exhausted": 0}
    got = report.set_pending_jobs(rows, spec, {"B": MAX_CASE_ATTEMPTS})
    assert got == {"errored": 0, "missing": [], "exhausted": 1}
    seq = [{"n": 1, "level": 1, "cases": ["A"]}, {"n": 2, "level": 2, "cases": ["B"]}]
    st = ladder.states(0.95, {1: None, 2: None}, seq, 0.9, terminal={1})
    assert st == {1: "errored", 2: "locked"}                  # blocks advancement
    assert ladder.due(0.95, {1: None}, seq, 0.9) == [1]       # still due while retryable
    assert 1 not in [n for n, v in st.items() if v == "pending"]   # never due again
    # a scored lower level cannot hide it: MAXED needs every set scored
    assert not all(x is not None for x in {1: None, 2: 0.95}.values())
    # an explicit re-run replaces the errored row: no exhausted job remains
    rows[1] = {"case_id": "B", "repeat": 1, "grade": "pass"}
    assert report.set_pending_jobs(rows, spec, {"B": MAX_CASE_ATTEMPTS})["exhausted"] == 0


def test_v2_case_judges_with_three_samples_without_an_id_list():
    pc = dict(judge.JUDGE_POLICY_DEFAULTS, samples3_rows=[], samples1_rows=[])
    row = {"case_id": "ZZ-V2", "rubric": "erp_deep", "v2": True, "judge_elements": []}
    assert judge.judge_policy(row, pc)["samples"] == 3
    other = {"case_id": "ZZ-OLD", "rubric": "story", "judge_elements": []}
    assert judge.judge_policy(other, pc)["kind"] != "override"


def test_inline_cases_inherit_the_set_v2_flag(tmp_path):
    spec = {"v2": True, "cases": [{"id": "Q1", "category": "nsfw"}, {"id": "Q2", "category": "nsfw", "v2": False}]}
    f = tmp_path / "nsfw" / "nsfw_rp"
    f.mkdir(parents=True)
    (f / "set-9.json").write_text(json.dumps(spec), encoding="utf-8")
    got = {c["id"]: c.get("v2") for c in ladder.inline_cases([tmp_path], include_drafts=True)}
    assert got == {"Q1": True, "Q2": False}


# ------------------------------------------------ spend cap, fix pass (S4)

def test_billed_error_is_charged_and_retry_is_guarded(capenv, monkeypatch):
    from crucibleforge import api
    capenv.setenv(spend.CAP_ENV, "1.3")
    p = providers.Provider(name="x", type="openai", base_url="http://127.0.0.1:9/v1",
                           prices={"m": {"input": 1.0, "output": 1.0}})
    spend._max_call[0] = 0.6                  # a call costs about 0.6 USD here
    calls = []

    def fake_stream(*a, **k):
        calls.append(1)
        raise api.StreamStalled("no bytes for 60s")
    monkeypatch.setattr(api, "stream_chat", fake_stream)
    monkeypatch.setattr(api, "MAX_TRANSPORT_RETRIES", 3)
    with pytest.raises(api.StreamStalled):
        p.chat("m", [{"role": "user", "content": "hi"}])
    assert spend.spent() == pytest.approx(0.6)       # the stalled attempt was billed
    assert spend._inflight[0] == 0.0                 # reservation released
    # 0.6 of 1.3 spent: one more call (0.6) fits; it stalls and is billed too
    with pytest.raises(api.StreamStalled):
        p.chat("m", [{"role": "user", "content": "hi"}])
    assert spend.spent() == pytest.approx(1.2)
    calls.clear()
    with pytest.raises(spend.SpendCapExceeded):      # over the cap: blocked before sending
        p.chat("m", [{"role": "user", "content": "hi"}])
    assert calls == []


def test_retry_rechecks_the_cap_before_resending(capenv, monkeypatch):
    from crucibleforge import api
    capenv.setenv(spend.CAP_ENV, "1")
    spend._max_call[0] = 0.6
    p = providers.Provider(name="x", type="openai", base_url="http://127.0.0.1:9/v1",
                           prices={"m": {"input": 1.0, "output": 1.0}})
    sent = []

    def fake_stream(*a, **k):
        sent.append(1)
        raise api.TransportError("HTTP 500 boom")        # retryable, billed estimate
    monkeypatch.setattr(api, "stream_chat", fake_stream)
    monkeypatch.setattr(api, "MAX_TRANSPORT_RETRIES", 5)
    monkeypatch.setattr(api, "RETRY_BACKOFF_S", 0)
    with pytest.raises(spend.SpendCapExceeded):
        p.chat("m", [{"role": "user", "content": "hi"}])
    assert len(sent) == 2          # 2nd attempt pushed spend to 1.2; the 3rd never went out


def test_concurrent_workers_cannot_overshoot_the_cap(capenv):
    import threading
    capenv.setenv(spend.CAP_ENV, "1")
    spend._max_call[0] = 0.4
    tokens, refused = [], []
    barrier = threading.Barrier(16)

    def worker():
        barrier.wait()
        try:
            tokens.append(spend.reserve("openai", "m"))
        except spend.SpendCapExceeded:
            refused.append(1)
    ts = [threading.Thread(target=worker) for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    # 0.4 each and the incoming call counts: only 2 reservations fit under 1.0
    assert len(tokens) == 2 and len(refused) == 14


def test_staged_runs_get_a_default_cap_only(monkeypatch, tmp_path):
    import argparse
    from crucibleforge import cli, config, ladder
    monkeypatch.delenv(spend.CAP_ENV, raising=False)
    monkeypatch.delenv(ladder.STAGED_ENV, raising=False)
    spend.reset()
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "scratch")
    cli._apply_process_switches(argparse.Namespace(include_staged=False, spend_cap_usd=None))
    assert spend.cap() is None                                   # live-board runs unchanged
    cli._apply_process_switches(argparse.Namespace(include_staged=True, spend_cap_usd=None))
    assert spend.cap() == spend.DEFAULT_STAGED_CAP_USD
    monkeypatch.setenv(spend.CAP_ENV, "3")
    cli._apply_process_switches(argparse.Namespace(include_staged=True, spend_cap_usd=None))
    assert spend.cap() == 3.0                                    # an explicit cap wins
    os.environ.pop(ladder.STAGED_ENV, None)
