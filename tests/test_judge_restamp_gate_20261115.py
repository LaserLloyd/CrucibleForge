"""The judge is never loaded to judge nothing (lead 2026-11-15, after 8a07881).

Rows pending only because their verdict was made under a judge thinking setting other than the
profile judge's, which this run's judge would stamp the same way again, are not work: the pre-judge
count (the rig gate) leaves them out, so a phase with nothing else pending does not load the judge,
and it says in one plain line how many such verdicts there are and what clears them."""
from __future__ import annotations

import json

import pytest

from crucibleforge import config, judge, judge_digest, profiles

PROF = "bench45"     # the thinking rule acts on 4.5 profiles (chains) only


def _row(i: int, thinking: bool | None) -> dict:
    r = {"bench_run_id": "r1", "case_id": f"C{i}", "repeat": 1, "turn": None, "rubric": "rp_single",
         "needs_judge": True, "prompt": "p", "response": f"x{i}", "profile": PROF}
    if thinking is not None:
        r["judge"] = {"judge_failed": False, "refused": False, "scores": {"prose": 5}}
        r["judge"]["instrument"] = judge_digest.instrument(r, "J", thinking)
    return r


def _cfg(thinking: bool) -> dict:
    return {"models": [{"name": "m1", "model_id": "m1", "provider": "p"}],
            "providers": {"p": {"type": "openai", "base_url": "http://x.invalid/v1"}},
            "defaults": {},
            "judge": {"samples": 1, "candidates": [{"provider": "p", "model_id": "J",
                                                    "thinking": thinking}]}}


@pytest.fixture
def other(tmp_path, monkeypatch):
    """A judge whose thinking differs from the profile judge's, and 3 rows it already judged."""
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc4-main")
    config.set_results_dir(tmp_path)
    prof = judge_digest._profile_settings(PROF)["thinking"]
    assert prof is not None
    t = not prof
    (tmp_path / "transcripts_m1.jsonl").write_text(
        "\n".join(json.dumps(_row(i, t)) for i in range(3)) + "\n", encoding="utf-8")

    def no_load(*a, **k):
        raise AssertionError("judge loaded")
    monkeypatch.setattr(judge, "_load_judge", no_load)
    return t


def test_the_gate_counts_no_restamp_and_says_why(other):
    assert judge.pending_judge_rows(["m1"]) == 3                       # what the board sees
    n, note = judge.judge_gate(_cfg(other), ["m1"])
    assert n == 0
    assert note and "3 verdict" in note and f"thinking {'on' if other else 'off'}" in note
    n, note = judge.judge_gate(_cfg(not other), ["m1"])                # the profile's setting: redo
    assert n == 3 and note is None
    n, _ = judge.judge_gate(_cfg(other), ["m1"], force=True)           # --force: everything
    assert n == 3


def test_run_judge_does_not_load_a_judge_for_restamps(other, caplog):
    out = judge.run_judge(_cfg(other), ["m1"])
    assert out["judged"] == 0 and out["judge"] is None


def test_a_real_pending_row_still_loads(other, tmp_path):
    with open(tmp_path / "transcripts_m1.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(_row(9, None)) + "\n")
    n, note = judge.judge_gate(_cfg(other), ["m1"])
    assert n == 1 and note and "3 verdict" in note
    with pytest.raises(AssertionError, match="judge loaded"):
        judge.run_judge(_cfg(other), ["m1"])
