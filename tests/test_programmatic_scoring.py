"""Judge-free scoring of multi-turn generated cases (programmatic: true)."""
import threading

import pytest

from crucibleforge import gen, judge, rpdeep, runner
from crucibleforge.gen._stack_common import split_turns
from crucibleforge.api import ChatResult

CASES = [("istack", {"level": 3, "variant": "conv"}), ("session_state", {"level": 3, "variant": "multi"}),
         ("istack", {"level": 2, "variant": "write"}), ("session_state", {"level": 2, "variant": "state"})]


def _case(fam, g):
    c = {"id": "P0", "category": "rpdeep", "generator": dict(g, type=fam)}
    return gen.materialize_case(c, "prog")


class _Ctx:
    lock = threading.Lock()

    def __init__(self, replies):
        self.replies = list(replies)

    def budget(self, n):
        return n

    def recover_for(self, case):
        return False

    def call(self, messages, **kw):
        return ChatResult(response_text=self.replies.pop(0), finish_reason="stop",
                          completion_tokens=50, served_model="m")


def _final(case, replies):
    rows = runner._run_multiturn(case, {"case_id": case["id"]}, _Ctx(replies), 2000, 0.7, 0.95, 1)
    return rows[-1], rows


def _replies(case, answer):
    parts = split_turns(answer)
    n = len(case["turns"])
    return (parts + [""] * n)[:n]


@pytest.mark.parametrize("fam,g", CASES)
def test_reference_scores_one_and_mutants_below_without_a_judge(fam, g):
    case = _case(fam, g)
    mod = gen.get(fam)
    gg = case["generator"]
    kw = {"release_seed": "prog", "case_id": "P0"}
    final, rows = _final(case, _replies(case, mod.reference(dict(gg), **kw)))
    assert not judge.needs_verdict(final)
    assert not judge.needs_verdict(final, force=False, retry_failed=True)
    assert final.get("rubric") is None
    res = rpdeep.score_row(final, case, rows)
    assert res["status"] == "ok" and res["rate"] == pytest.approx(1.0)
    for m in mod.mutants(dict(gg), **kw):
        f2, r2 = _final(case, _replies(case, m))
        res = rpdeep.score_row(f2, case, r2)
        assert res["status"] == "ok" and res["rate"] < 1.0


@pytest.mark.parametrize("fam,g", CASES[:2])
def test_score_row_needs_no_judge_verdict_at_all(fam, g):
    case = _case(fam, g)
    final, rows = _final(case, ["no"] * len(case["turns"]))
    final.pop("judge", None)
    res = rpdeep.score_row(final, case, rows)
    assert res["status"] == "ok" and res["rate"] is not None and res["rate"] < 0.5


def test_runner_never_queues_a_programmatic_row_for_the_judge():
    case = _case("istack", {"level": 2, "variant": "conv"})
    final, _ = _final(case, ["x"] * len(case["turns"]))
    assert not judge.needs_verdict(final, retry_failed=True)
    assert (final.get("judge") or {}).get("programmatic") is True


def test_error_row_of_a_programmatic_case_is_not_judged_either():
    case = _case("istack", {"level": 2, "variant": "conv"})
    assert rpdeep.is_programmatic(case)
    assert not rpdeep.is_programmatic({"id": "z", "turns": ["a"], "judge_elements": [{"id": "e"}]})
