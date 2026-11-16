"""Degenerate-output guard (2026-11-14).

The 8B instance on the rig went degenerate: its "Hi" probe returned 16,384 tokens of
"════" and two runs went on and stored zeros as scores. Now (1) the run-start probe must
be a plausible short reply or the run fails before any scored item is sent, and (2) the
early stop counts a "degenerate" class from the first answers.

Fixture: the quarantined rows (results-ds3v, results-calib-ds8b, results-ds3l) and the
same model's sane run (results-ds3), long texts trimmed to their head and 5,800-char tail
(the test reads only the last 4,096 characters).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crucibleforge import config, degenerate, earlystop, providers, runner, stopflag
from crucibleforge.api import ChatResult

FIX = Path(__file__).resolve().parent / "fixtures" / "degenerate_8b_20261114.json"
ROWS = json.loads(FIX.read_text(encoding="utf-8"))


def _src(prefix):
    return [r for r in ROWS if r["src"].startswith(prefix)]


def _scored(rows):
    return [r for r in rows if r.get("category") != "perf"]


# ------------------------------------------------------------ the text test


def test_floods_and_short_ngram_loops_are_degenerate():
    assert degenerate.text_degenerate("═" * 5000)["ratio"] < 0.02
    assert degenerate.text_degenerate("and the, " * 600)
    assert degenerate.text_degenerate("10, " * 900)


def test_ordinary_text_code_and_short_replies_are_not():
    import random
    rng = random.Random(7)
    words = ("ferry bell crate river jetty clerk ledger salt iron cloth tide marsh hut chart key "
             "morning rain lamp rope harbour north south wind gull tar oar sail").split()
    prose = " ".join(rng.choice(words) + ("." if rng.random() < 0.1 else "") for _ in range(1400))
    code = "\n".join(f"def f{i}(x):\n    return x * {i} + {i * i % 17}" for i in range(150))
    assert degenerate.text_degenerate(prose) is None
    assert degenerate.text_degenerate(code) is None
    assert degenerate.text_degenerate("═" * 100) is None          # too short to judge (MIN_CHARS)
    assert degenerate.text_degenerate("") is None


def test_every_quarantined_degenerate_row_is_caught_and_the_sane_run_is_clean():
    bad = {(r["src"], r["case_id"]): degenerate.row_degenerate(r) for r in _src("bad:")}
    # the "════" probe floods
    for src in ("bad:ds3v", "bad:calib-ds8b", "bad:ds3l"):
        assert bad[(src, "P1-tiny")]["channel"] == "answer"
    assert bad[("bad:calib-ds8b", "P2-short")]
    # loops in the reasoning channel of a reply that never answered
    for cid in ("FLAT-patchfix5_L5-003-r1-a1", "FLAT-patchfix5_L5-001-r1-a1", "FLAT-keyfunc7x2_L7-001-r1-a1"):
        assert bad[("bad:ds3v", cid)], cid
    for cid in ("FLAT-istack_cal_L2-001-r1-a1", "FLAT-istack_cal_L7-001-r1-a1"):
        assert bad[("bad:calib-ds8b", cid)], cid
    assert bad[("bad:ds3l", "FLAT-istack_write_L4-001-r1-a1")]["channel"] == "answer"   # cut content loop
    # short garbage is not "degenerate" (it is blank / wrong, judged elsewhere)
    assert bad[("bad:ds3v", "FLAT-patchfix5_L5-002-r1-a1")] is None
    sane = _src("sane:")
    assert len(sane) > 70
    assert [r["case_id"] for r in sane if degenerate.row_degenerate(r)] == []


def test_a_finished_answer_after_a_looping_think_is_judged_on_the_answer():
    row = {"response": "The answer is 42.", "reasoning": "and the, " * 900, "finish_reason": "stop"}
    assert degenerate.row_degenerate(row) is None


# ------------------------------------------------------------ run-start probe


def test_the_degenerate_probes_fail_the_run_with_a_clear_reason():
    for src in ("bad:ds3v", "bad:calib-ds8b", "bad:ds3l"):
        rec = degenerate.probe_failure([r for r in _src(src) if r.get("category") == "perf"])
        assert rec and rec["case_id"] == "P1-tiny" and rec["signal"] == "probe", src
        assert "hit its ceiling (16384 tokens" in rec["reason"]
        assert "degenerate" not in rec["reason"] or rec["degenerate"]["ratio"] < 0.1
        assert "no scored item was sent" in rec["reason"]


def test_the_sane_probes_pass():
    assert degenerate.probe_failure([r for r in _src("sane:") if r.get("category") == "perf"]) is None


@pytest.mark.parametrize("row,why", [
    ({"case_id": "P1-tiny", "response": "Hi! " * 3, "finish_reason": "length"}, "ceiling"),
    ({"case_id": "P1-tiny", "response": "", "reasoning": "", "finish_reason": "stop"}, "no answer"),
    ({"case_id": "P3-gen", "response": "ab" * 2000, "finish_reason": "stop"}, "degenerate repetition"),
])
def test_each_probe_rule(row, why):
    ok = {"case_id": "P2-short", "response": "A computer computes.", "finish_reason": "stop"}
    rec = degenerate.probe_failure([ok, row])
    assert rec and why in rec["reason"] and rec["case_id"] == row["case_id"]


def test_error_rows_are_left_to_the_transport_path():
    assert degenerate.probe_failure([{"case_id": "P1-tiny", "error": "boom", "response": ""}]) is None


# ------------------------------------------------------------ early stop


def test_early_stop_trips_degenerate_on_both_stored_runs():
    for src in ("bad:ds3v", "bad:calib-ds8b"):
        rec = earlystop.replay(_scored(_src(src)), {"defaults": {"early_stop": dict(earlystop.DEFAULTS)}})
        assert rec["tripped"] and "degenerate" in rec["signals"], (src, rec)
        assert rec["after_answers"] <= 5
        assert "degenerate" in earlystop.describe(rec)


def test_early_stop_degenerate_is_quiet_on_the_sane_run():
    rec = earlystop.replay(_scored(_src("sane:")), {"defaults": {"early_stop": dict(earlystop.DEFAULTS)}})
    assert not rec.get("tripped") and rec["counts"]["degenerate"] == 0


def test_one_degenerate_row_among_good_ones_does_not_trip():
    good = {"response": "ok " * 10, "finish_reason": "stop"}
    loop = {"response": "═" * 3000, "finish_reason": "stop"}
    rec = earlystop.replay([loop] + [good] * 29, {"defaults": {"early_stop": dict(earlystop.DEFAULTS)}})
    assert not rec.get("tripped") and rec["counts"]["degenerate"] == 1


def test_degenerate_threshold_can_be_unset_report_only():
    loop = {"response": "═" * 3000, "finish_reason": "stop"}
    cfg = {"defaults": {"early_stop": {**earlystop.DEFAULTS, "degenerate_rate_gt": None}}}
    rec = earlystop.replay([loop] * 30, cfg)
    assert not rec.get("tripped") and rec["counts"]["degenerate"] == 30


# ------------------------------------------------------------ the runner


@pytest.fixture(autouse=True)
def _flags():
    stopflag.ABORT.clear()
    runner.STOP.clear()
    providers.clear_cache()
    yield
    providers.clear_cache()


def _fake_rig(monkeypatch, mode):
    sent = []

    def chat(self, model_id, messages, **kw):
        text = messages[-1]["content"]
        sent.append((model_id, text))
        if mode == "flood" and model_id == "bad":
            return ChatResult(response_text="═" * 3000, finish_reason="length", served_model=model_id,
                              completion_tokens=16384, total_s=0.1)
        return ChatResult(response_text="ok there", finish_reason="stop", served_model=model_id,
                          completion_tokens=3, total_s=0.1)

    P = providers.Provider
    monkeypatch.setattr(P, "alive", lambda self: True)
    monkeypatch.setattr(P, "is_available", lambda self, mid: True)
    monkeypatch.setattr(P, "switch_model", lambda self, model_id, ctx=None: 1.0)
    monkeypatch.setattr(P, "chat", chat)
    monkeypatch.setattr(P, "live_context", lambda self, mid: None)
    monkeypatch.setattr(P, "trained_context", lambda self, mid: None)
    monkeypatch.setattr(P, "loaded_plan_for", lambda self, mid: {"parallel": 1})
    monkeypatch.setattr(P, "workers", lambda self, mid: 1)
    monkeypatch.setattr(P, "is_loaded", lambda self, mid: True)
    monkeypatch.setattr(P, "model_capabilities", lambda self, mid: set())
    return sent


def _run(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    sent = _fake_rig(monkeypatch, mode)
    cfg = {"defaults": {"context_length": 32768, "repeats": {"instruct": 1, "perf": 1},
                        "early_stop": dict(earlystop.DEFAULTS)},
           "providers": {"p": {"type": "studioforge", "base_url": "http://p.invalid/v1"}},
           "judge": {"candidates": []}, "models": []}
    perf = [{"id": "P1-tiny", "category": "perf", "prompt": "Hi", "max_tokens": 16384}]
    cases = perf + [{"id": f"I{i:02d}", "category": "instruct", "prompt": f"say ok {i}", "max_tokens": 64,
                     "grader": "contains", "grader_config": {"needles": ["ok"]}} for i in range(6)]
    entries = [{"name": "bad", "model_id": "bad", "provider": "p"},
               {"name": "good", "model_id": "good", "provider": "p"}]
    return sent, runner.run_models(cfg, entries, cases, skip_conformance=True)


def test_a_degenerate_probe_stops_the_model_before_any_scored_item(tmp_path, monkeypatch):
    sent, summary = _run(tmp_path, monkeypatch, "flood")
    bad = json.loads((tmp_path / "meta_bad.json").read_text(encoding="utf-8"))
    assert bad["failed"] is True and bad["publishable"] is False
    assert bad["early_stop"]["signal"] == "probe" and "P1-tiny" in bad["error"]
    assert "hit its ceiling" in bad["error"]
    assert [r["case_id"] for r in config.load_transcripts("bad")] == ["P1-tiny"]
    assert not [t for m, t in sent if m == "bad" and t.startswith("say ok")]   # nothing scored was sent
    assert [t for m, t in sent if m == "good" and t.startswith("say ok")]
    good = json.loads((tmp_path / "meta_good.json").read_text(encoding="utf-8"))
    assert good["failed"] is False and len(config.load_transcripts("good")) == 7
    assert summary["bad"]["early_stop"]["signal"] == "probe"


def test_a_sane_probe_runs_on_and_rows_carry_no_stamp(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch, "sane")
    rows = config.load_transcripts("bad")
    assert len(rows) == 7 and not any(r.get("degenerate") for r in rows)
    assert not any("stage" in r for r in rows)                    # only a routed run carries a stage


def test_a_degenerate_row_is_stamped():
    row = {"case_id": "X", "response": "═" * 3000, "finish_reason": "stop"}
    runner.stamp_degenerate(row)
    assert row["degenerate"]["channel"] == "answer"
    ok = {"case_id": "Y", "response": "fine", "finish_reason": "stop"}
    runner.stamp_degenerate(ok)
    assert "degenerate" not in ok
