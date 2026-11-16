"""The thinking part of the rubric stamp acts on 4.5 profiles only (lead 2026-11-15).

4.5 work must not change what the 4.1 profile does: under `bench` a verdict's thinking setting is
recorded but never makes it stale, so judging there is what it was at af52930. A 4.5 profile is one
whose composition has chains (the same test the weighted shares and PUBOUT's fields use)."""
from __future__ import annotations

import pytest

from crucibleforge import judge, judge_digest, profiles


def _row(profile: str | None, thinking: bool) -> dict:
    r = {"bench_run_id": "r1", "case_id": "C1", "repeat": 1, "rubric": "rp_single",
         "needs_judge": True, "prompt": "p", "response": "x",
         "judge": {"judge_failed": False, "refused": False, "scores": {"prose": 5}}}
    if profile:
        r["profile"] = profile
    r["judge"]["instrument"] = judge_digest.instrument(r, "J", thinking)
    return r


def test_bench_records_thinking_but_never_goes_stale_on_it(monkeypatch):
    monkeypatch.delenv(profiles.COMPOSITION_ENV, raising=False)
    want = judge_digest._profile_settings("bench")["thinking"]
    for prof in (None, "bench"):
        r = _row(prof, not want)
        assert r["judge"]["instrument"]["thinking"] is (not want)          # recorded
        assert judge_digest.stale_reason(r) is None
        assert not judge.needs_verdict(r)


def test_a_45_profile_with_chains_still_goes_stale_on_it(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc4-main")
    want = judge_digest._profile_settings("bench45")["thinking"]
    assert want is not None
    r = _row("bench45", not want)
    assert judge_digest.stale_reason(r) == "thinking"
    assert judge_digest.stale_reason(_row("bench45", want)) is None


def test_a_rubric_change_still_makes_a_bench_verdict_stale(monkeypatch):
    monkeypatch.delenv(profiles.COMPOSITION_ENV, raising=False)
    r = _row("bench", True)
    r["judge"]["instrument"]["rubric_sha"] = "0" * 16
    assert judge_digest.stale_reason(r) == "rubric"
