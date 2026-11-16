"""A verdict the running judge would write again unchanged is not that run's work (2026-11-15).

The stale-thinking rule (f5b0f7f) made a fresh verdict "stale" the moment it was written whenever the
running judge's thinking setting differs from the profile judge's: a pool/shard copy then took rows
the other copy had just judged (duplicates in test_parallel_judge_20261108) and a phase could keep
re-taking them (timeouts). The board still reads such a verdict as stale."""
from __future__ import annotations

from crucibleforge import judge, judge_digest, profiles

PROF = "bench45"     # the thinking rule acts on 4.5 profiles (chains) only


def _row(thinking: bool) -> dict:
    r = {"bench_run_id": "r1", "case_id": "C1", "repeat": 1, "rubric": "rp_single",
         "needs_judge": True, "prompt": "p", "response": "x", "profile": PROF,
         "judge": {"judge_failed": False, "refused": False, "scores": {"prose": 5}}}
    r["judge"]["instrument"] = judge_digest.instrument(r, "J", thinking)
    return r


def test_only_a_restamp_by_the_same_setting_is_skipped(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc4-main")
    prof_thinking = judge_digest._profile_settings(PROF)["thinking"]
    assert prof_thinking is not None
    other = not prof_thinking
    r = _row(other)
    assert judge_digest.stale_reason(r) == "thinking"          # the board: stale
    assert judge.needs_verdict(r)                               # a judge with the profile's setting redoes it
    assert judge.restamp_only(r, other, {}) is True             # the same setting again: not this run's work
    assert judge.restamp_only(r, prof_thinking, {}) is False    # the profile's setting: redo it
    fresh = _row(prof_thinking)
    assert not judge.needs_verdict(fresh) and judge.restamp_only(fresh, other, {}) is False
    assert judge.restamp_only({**r, "judge": None}, other, {}) is False
