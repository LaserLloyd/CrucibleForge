"""The shipping composition is real (lead 2026-11-15, the cut).

``standard`` = rc4-main under its release name; ``composition_aliases: {rc4-main: standard}`` keeps every stored
row and run meta that says rc4-main resolving to the same test; ``default_composition: standard`` makes a bare
``crucibleforge all --profile bench45`` (and after the flip a bare ``crucibleforge all``) run the shipping test with
no CF_COMPOSITION set; ``judge_stamp_required: true`` there (an unstamped verdict does not count). rc3-main and the
screen-* compositions stay in the file, retired / development, and are never the default."""
from __future__ import annotations

import argparse

import pytest

from crucibleforge import config, judge_digest, profiles, version

pytestmark = [pytest.mark.live_ladder, pytest.mark.shipping_default]
CFG = config.load_config(config.EXAMPLE_CONFIG_PATH)


@pytest.fixture
def bare(monkeypatch):
    monkeypatch.delenv(profiles.COMPOSITION_ENV, raising=False)


def _raw():
    return profiles._yaml_file(profiles.PROFILES_DIR / "bench45.yaml")


def test_standard_is_the_default_and_rc4_main_its_alias():
    raw = _raw()
    assert raw["default_composition"] == "standard"
    assert raw["composition_aliases"].get("rc4-main") == "standard"
    assert "standard" in raw["compositions"] and "rc4-main" not in raw["compositions"]
    for kept in ("rc3-main", "rc2-main"):
        assert kept in raw["compositions"]                      # retired, still in the file


def test_a_bare_run_is_the_shipping_test(bare):
    from crucibleforge import cli
    for name in ("bench45", profiles.DEFAULT_PROFILE):          # --profile bench45, and the bare command after the flip
        args = argparse.Namespace(profile=None if name == profiles.DEFAULT_PROFILE else name, smoke=False, judge=None)
        _, _, prof = cli._apply_profile_arg(args, CFG)
        assert prof["name"] == "bench45" and prof["composition"] == "standard"
        assert prof.get("chains")                               # the 4.5-only rules key on this
        assert not prof.get("reuse")                            # no 4.1 row counts
        assert prof.get("judge_stamp_required") is True
    assert version.SUITE_VERSION == "4.5.1" and version.revision().startswith("4.5.1+")
    st = judge_digest._profile_settings("bench45")
    assert st["is45"] and st["stamp_required"]


def test_rc4_main_resolves_to_the_same_test(bare, monkeypatch):
    std = profiles.load_profile("bench45", CFG)
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc4-main")
    old = profiles.load_profile("bench45", CFG)
    assert old["composition"] == "standard"
    for k in ("cases", "chains", "scores", "core_level", "core_generated", "main_nsfw", "pack_exclude",
              "case_repeats", "pending_scaling", "reuse", "judge_stamp_required"):
        assert old.get(k) == std.get(k), k


def test_an_unstamped_verdict_does_not_count_on_standard(bare):
    row = {"bench_run_id": "r", "case_id": "C1", "repeat": 1, "rubric": "rp_single", "profile": "bench45",
           "needs_judge": True, "judge": {"judge_failed": False, "scores": {"prose": 7}}}
    assert judge_digest.stale_reason(row) == "unstamped"
    row["judge"]["instrument"] = judge_digest.instrument(row, "J", judge_digest._profile_settings("bench45")["thinking"])
    assert judge_digest.stale_reason(row) is None


def test_no_retired_or_development_composition_is_the_default():
    """The profile file is the only place the default is set (profile-only cut, no code guard): this test is the
    guard. rc*/main-* (retired) and screen-*/calib-*/pack*-/pair-* (development) never ship by default."""
    raw = _raw()
    dflt = raw["default_composition"]
    assert dflt == "standard"
    retired_or_dev = [c for c in raw["compositions"] if c != "standard"]
    assert dflt not in retired_or_dev and "rc3-main" in retired_or_dev
    assert all(not str(v).startswith(("screen-", "calib-")) for v in raw["composition_aliases"].values())
