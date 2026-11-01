"""Follow-ups to the 2026-10-26 code-bank review (budget regime, denominator,
paired comparison, per-category stamps, server rejections)."""
import json

import pytest

from crucibleforge import report, runner, version


class _C:
    def __init__(self, bank, ctx_len):
        self.bank_budget, self.ctx_len = bank, ctx_len


def test_bank_budget_is_generous_and_clamped_to_the_window():
    assert runner.bank_budget(_C(65536, None), 5000) == 65536
    assert runner.bank_budget(_C(65536, 1_000_000), 5000) == 65536
    assert runner.bank_budget(_C(65536, 32768), 5000) == 32768 - 5000 - runner.BANK_WINDOW_SLACK
    assert runner.bank_budget(_C(0, 32768), 5000) is None


def test_missing_tasks_count_zero_and_flag_incomplete():
    rows = [{"case_id": "a", "grade": "pass", "score": 1.0}, {"case_id": "b", "grade": "error"}]
    s = report.bank_stats(rows, expected={"a", "b"})
    assert s["score"] == 0.5 and not s["complete"] and s["tasks_missing"] == ["b"] and s["tasks"] == 2


def test_paired_diff():
    a = {"t1": 1.0, "t2": 1.0, "t3": 0.5, "t4": 1.0}
    b = {"t1": 0.0, "t2": 0.5, "t3": 0.5, "t4": 0.0}
    d, lo, hi, n = report.paired_diff(a, b)
    assert n == 4 and d == pytest.approx(0.625) and lo > 0
    assert report.paired_diff({"x": 1}, {"x": 0}) is None


def test_bank_files_outside_the_global_hash():
    assert "codebank" in version.BANK_CATEGORIES
    h = version.bank_case_hash("codebank", "CB-A1-tabsplit-cents")
    row = {"category": "codebank", "case_id": "CB-A1-tabsplit-cents", "bank_revision": h,
           "bench_revision": f"{version.SUITE_VERSION}+deadbeef"}
    assert version.bank_row_current(row)
    assert not version.bank_row_current({**row, "bank_revision": "00000000"})
    assert not version.bank_row_current({**row, "bench_revision": "3.7.0+deadbeef"})


class _RejectCtx:
    ctx_len = None
    bank_budget = 0

    def call(self, messages, **kw):
        raise runner.RequestRejected(400, "context window exceeded")

    def budget(self, n):
        return n

    def recover_for(self, case):
        return False


def test_agent_request_rejected_on_first_turn_is_a_graded_fail():
    case = json.loads((runner.Path(__file__).resolve().parent.parent / "cases" / "codeagent.json").read_text(encoding="utf-8"))[0]
    row = runner._run_env_loop({**case, "category": "codeagent"}, {"case_id": case["id"]}, _RejectCtx(), 100, None, 1)
    assert row["grade"] == "fail" and row["score"] == 0.0 and "rejected" in row["agent"]["end"]


def test_bank_rows_never_escalate():
    row = {"category": "codebank", "case_id": "CB-X", "repeat": 1, "finish_reason": "length",
           "thinking": True, "max_tokens_sent": 65536, "grade": "fail"}
    assert runner.escalation_jobs([row], 1 << 30) == set()
    assert runner.escalation_jobs([{**row, "category": "reasoning"}], 1 << 30) == {(None, "CB-X", 1)}


def test_anchor_tasks_run_but_are_not_scored(monkeypatch):
    prof = ({"defaults": {"bank_anchors": ["CB-A"]}},
            [{"id": "CB-A", "category": "codebank"}, {"id": "CB-B", "category": "codebank"}])
    monkeypatch.setattr(report, "_bank_profile", lambda cfg: prof)
    base = {"profile": "bench", "category": "codebank", "repeat": 1, "ts": "2026-10-26T00:00:00",
            "bench_revision": "x", "metrics": {}}
    rows = [{**base, "case_id": "CB-A", "grade": "pass", "score": 1.0},
            {**base, "case_id": "CB-B", "grade": "fail", "score": 0.25}]
    s = report.model_stats("m", cfg={}, rows=rows, meta={})
    assert s["codebank"]["score"] == 0.25 and s["codebank"]["tasks"] == 1
    assert s["bank_anchor"]["tasks"] == 1 and s["bank_anchor"]["score"] == 1.0
    assert report._bank_expected({}, "codebank") == {"CB-B"}
