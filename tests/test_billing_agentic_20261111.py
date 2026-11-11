"""B2b (Fable 5.2 LAG1-3, 2026-11-11): a provider billing refusal that arrives as
HTTP 400 inside an agentic episode must never be graded as the model's 0.0.
It is an infra/pending row: the pack reads pending, and the ladder re-queues it."""
from __future__ import annotations

import json

import pytest

from crucibleforge import bench_all, cli, preflight, report, runner
from crucibleforge.api import RequestRejected
from test_billing_pending_41 import PACK
from test_points_board_39 import _board, _row, _rows, _set_file, _setup, _work_only

CREDIT = ('{"type":"error","error":{"type":"invalid_request_error","message":'
          '"Your credit balance is too low to access the Anthropic API."}}')
END = "request rejected mid-episode: " + f"HTTP 400: {CREDIT}"[:120]


class _Ctx:
    ctx_len = None
    bank_budget = 0

    def __init__(self, status, body):
        self.exc = RequestRejected(status, body)

    def call(self, messages, **kw):
        raise self.exc

    def budget(self, n):
        return n

    def recover_for(self, case):
        return False


def _agent_case():
    p = runner.Path(__file__).resolve().parent.parent / "cases" / "codeagent.json"
    return {**json.loads(p.read_text(encoding="utf-8"))[0], "category": "codeagent"}


def _episode_fail_row(cid, rep=1, end=END):
    return _row(cid, "coding", rep, True, run="lad", score=0.0,
                agent={"turns": 0, "tool_calls": 0, "end": end})


# 1. the runner ------------------------------------------------------------

@pytest.mark.parametrize("status", [400, 401, 402, 403, 429])
def test_a_billing_refusal_in_the_episode_loop_is_raised_not_graded(status):
    case = _agent_case()
    with pytest.raises(RequestRejected):
        runner._run_env_loop(case, {"case_id": case["id"]}, _Ctx(status, CREDIT), 100, None, 1)


def test_a_context_400_in_the_episode_loop_is_still_graded():
    case = _agent_case()
    row = runner._run_env_loop(case, {"case_id": case["id"]},
                               _Ctx(400, "context window exceeded"), 100, None, 1)
    assert row["grade"] == "fail" and "rejected" in row["agent"]["end"]


def test_a_billing_400_on_a_bank_case_is_a_transport_row_not_a_generation_fail():
    bank = {"id": "CB-X", "category": "codeagent"}
    assert runner._rejection_kind(bank, RequestRejected(400, CREDIT)) == "transport"
    assert runner._rejection_kind(bank, RequestRejected(400, "bad field")) == "generation"
    assert runner._rejection_kind({"id": "a", "category": "reasoning"},
                                  RequestRejected(400, "bad field")) == "transport"


def test_billing_answer_covers_every_status_and_needs_the_wording():
    for st in (400, 401, 402, 403, 429):
        assert preflight._is_billing_answer(st, CREDIT)
    assert not preflight._is_billing_answer(401, "invalid api key")
    assert not preflight._is_billing_answer(400, "context window exceeded")


# 2. the stored row --------------------------------------------------------

def test_a_stored_episode_fail_row_with_the_credit_400_is_billing():
    assert preflight.billing_error_row(_episode_fail_row("x"))
    assert not preflight.billing_error_row(
        _episode_fail_row("x", end="request rejected mid-episode: HTTP 400: context window exceeded"))
    assert not preflight.billing_error_row(
        _episode_fail_row("x", end="turn limit"))


def test_done_jobs_does_not_count_a_billing_row():
    rows = [_episode_fail_row("A"), _row("B", "coding", 1, True)]
    assert cli.done_jobs(rows) == {("B", 1)}


# 3. the board and the ladder ----------------------------------------------

def test_an_episode_billing_pack_is_pending_and_due(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    _set_file(env, "work/code", 1, PACK)
    rows = _rows() + [_episode_fail_row(c, r) for c in PACK for r in (1, 2)]
    st = _board(env, {"m": rows})["m"]
    assert st["packs_blocked"] == ["work/code#1"]
    assert st["provisional"] and st["scorecard"]["total"] is None
    det = st["scorecard"]["ladder"]["work"]["code"]
    assert det["sets"][0]["state"] == "pending"
    due = bench_all.ladder_due(env.cfg, "m", bench_all.error_attempts("m"))
    assert due and due["jobs"] >= len(PACK)


def test_a_real_episode_fail_still_scores_zero(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    _set_file(env, "work/code", 1, PACK)
    rows = _rows() + [_episode_fail_row(c, r, end="turn limit") for c in PACK for r in (1, 2)]
    st = _board(env, {"m": rows})["m"]
    assert "packs_blocked" not in st
    assert st["scorecard"]["ladder"]["work"]["code"]["sets"][0]["state"] == "scored"
