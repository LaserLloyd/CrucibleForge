"""The release flip (ship plan C8, lead 2026-11-15; applied at the cut, not before).

At release bench45 (with its ``standard`` composition) becomes what a bare command selects, and the 4.1
profile ``bench`` stays selectable with ``--profile bench``. Two names keep the two meanings apart:
``DEFAULT_PROFILE`` = the CLI's choice when ``--profile`` is not given (flips to bench45);
``ARCHIVE_PROFILE`` = the 4.1 benchmark, what a stored row or verdict without a profile was made under,
and the profile the 4.1-only rules key on (release-seed fallback, code-bank clamp floor, the verdict
regrade gate, the escalation and tier-2/3 phases). It never moves, so the flip changes nothing about
how either profile's rows are stamped, judged or scored. Here the flip is simulated by patching
DEFAULT_PROFILE; the constant itself moves only in the cut commit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from crucibleforge import cli, config, judge, judge_digest, profiles, report

FIX = Path(__file__).parent / "fixtures" / "board41"


@pytest.fixture
def flipped(monkeypatch):
    monkeypatch.setattr(profiles, "DEFAULT_PROFILE", "bench45")
    monkeypatch.setattr(profiles, "_ACTIVE", [None])
    monkeypatch.delenv(profiles.COMPOSITION_ENV, raising=False)
    judge_digest._PROF_CACHE.clear()
    yield
    judge_digest._PROF_CACHE.clear()


def test_the_archive_profile_is_the_4_1_benchmark():
    assert profiles.ARCHIVE_PROFILE == "bench"
    assert profiles.load_profile(profiles.ARCHIVE_PROFILE).get("chains") is None   # 4.1: no chain units


@pytest.mark.live_ladder      # a bare command is the shipping `standard` composition since the cut: it reads the live ladder
def test_a_bare_command_selects_the_default_and_bench_stays_selectable(flipped):
    assert profiles.active_name() == "bench45"
    _cfg, _cases, prof = cli._apply_profile_arg(argparse.Namespace(profile=None, smoke=False, judge=None),
                                                {"defaults": {}, "models": [], "providers": {}})
    assert prof["name"] == "bench45"
    _cfg, _cases, prof = cli._apply_profile_arg(argparse.Namespace(profile="bench", smoke=False, judge=None),
                                                {"defaults": {}, "models": [], "providers": {}})
    assert prof["name"] == "bench"


def test_a_row_without_a_profile_is_read_as_4_1(flipped, monkeypatch):
    seen = []
    real = judge_digest._profile_settings

    def spy(name):
        seen.append(name)
        return real(name)
    monkeypatch.setattr(judge_digest, "_profile_settings", spy)
    row = {"case_id": "JC-RP-1", "rubric": "real_person", "element_standard": 45, "repeat": 1,
           "judge": {"judge_failed": False, "scores": {"real_person": 8}}}
    judge_digest.stale_reason(row)
    assert seen == ["bench"]
    seen.clear()
    judge_digest.stale_reason({**row, "profile": "bench45"})
    assert seen == ["bench45"]


def test_the_4_1_only_rules_stay_on_bench(flipped, monkeypatch):
    from crucibleforge import gen
    gen._PROFILE_SEED.clear()
    flipped_seed = gen.release_seed()
    monkeypatch.setattr(profiles, "DEFAULT_PROFILE", "bench")
    gen._PROFILE_SEED.clear()
    assert gen.release_seed() == flipped_seed                    # the fallback reads bench.yaml either way
    monkeypatch.setattr(profiles, "DEFAULT_PROFILE", "bench45")
    # the tier-2/3 phases key on the archive profile: bench45 (the new default) never enters them
    called = []
    monkeypatch.setattr(profiles, "load_profile", lambda *a, **k: called.append(a) or {})
    args = argparse.Namespace(profile=None, smoke=False, no_tiers=False)
    assert cli._tier_phases(args, {"defaults": {}}, ["m"]) == 0 and called == []


def test_the_4_1_golden_board_is_unchanged_under_the_flip(flipped, tmp_path, monkeypatch):
    rows = json.loads((FIX / "rows.json").read_text(encoding="utf-8"))
    for label, rs in rows.items():
        (tmp_path / f"transcripts_{label}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rs),
                                                             encoding="utf-8")
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    profiles.set_active("bench")                                   # `report --profile bench`
    want = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))["board"]
    labels, stats, _ = report.board_stats(None, None)
    assert sorted(labels) == sorted(want)
    for l in labels:
        got, exp = stats[l]["scorecard"], want[l]
        assert (got.get("total") is None and exp["total"] is None) or abs(got["total"] - exp["total"]) < 1e-9, l
        for score, ps in exp["parts"].items():
            for part, v in ps.items():
                g = (got.get("parts") or {}).get(score, {}).get(part)
                assert (g is None and v is None) or abs(float(g) - float(v)) < 1e-9, (l, score, part)


def test_the_verdict_regrade_gate_keys_on_the_archive_profile(flipped, monkeypatch):
    """judge.verdict_outdated: a 4.1 (bench) row keeps its grading / samples re-judge; a profile-less row is a
    4.1 row. Before the split this gate read DEFAULT_PROFILE, so the flip would have silently switched it
    off for bench and on for bench45."""
    monkeypatch.setattr(judge, "verdict_samples", lambda j: 0)
    import crucibleforge.version as V
    monkeypatch.setattr(V, "row_current", lambda r, cfg=None: True)
    monkeypatch.setattr(V, "regrade_needed", lambda r: True)
    row = {"case_id": "X", "judge": {"judge_failed": False, "scores": {}}}
    assert judge.verdict_outdated(row) == "grading"
    assert judge.verdict_outdated({**row, "profile": "bench"}) == "grading"
