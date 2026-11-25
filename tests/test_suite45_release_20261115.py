"""What ships is called 4.5 (ship plan C8, lead 2026-11-15; patch for the lead's decision, not applied).

1. The harness stamps 4.5.0 (version.SUITE_VERSION, pyproject): new rows read ``bench_revision`` "4.5.0+<hash>".
2. Stored rows need neither a re-run nor a restamp. Currency is per case / chain revision under the regime
   floor (3.9.0), never the suite prefix, so a 4.1.0-stamped row stays current; on the board a row made by a
   profile that declares its release (bench45 ``release_seed: "4.5.0"``) shows that release, so tonight's
   bench45 rows print 4.5.0. 4.1 rows (profile ``bench``) keep printing 4.1.0.
3. The composition that ships has no "rc" in it: ``default_composition`` picks it when $CF_COMPOSITION is
   unset, and ``composition_aliases`` keeps tonight's name (``rc3-main``) working for scripts and metas.
   Rows do not carry the composition name and no set name changes, so every stored row stays current."""
from __future__ import annotations

import pytest

from crucibleforge import profiles, report, version
from crucibleforge.config import ConfigError


def test_the_harness_stamps_45():
    assert version.SUITE_VERSION == "4.5.1"
    assert version.revision().startswith("4.5.1+")


def test_a_41_stamped_bench45_row_is_still_current_by_regime():
    assert version.in_regime({"bench_revision": "4.1.0+3d9be8cc"})


def test_bench45_rows_made_before_the_bump_show_the_release():
    rows = [{"bench_revision": "4.1.0+3d9be8cc", "profile": "bench45"},
            {"bench_revision": "4.5.0+aaaaaaaa", "profile": "bench45"}]
    assert report.suites_of(rows) == ["4.5.0"]


def test_41_rows_keep_41():
    assert report.suites_of([{"bench_revision": "4.1.0+3d9be8cc", "profile": "bench"}]) == ["4.1.0"]
    assert report.suites_of([{"bench_revision": "4.1.0+3d9be8cc"}]) == ["4.1.0"]


def _toy(**extra):
    return {"name": "t", "cases": {"x": ["a"]},
            "compositions": {"standard": {"cases": {"x": ["b"]}}}, **extra}


def test_default_composition_applies_without_the_env(monkeypatch):
    monkeypatch.delenv(profiles.COMPOSITION_ENV, raising=False)
    out = profiles.apply_composition(_toy(default_composition="standard"), "t")
    assert out["composition"] == "standard" and out["cases"] == {"x": ["b"]}


def test_an_alias_selects_the_renamed_composition(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc3-main")
    out = profiles.apply_composition(_toy(composition_aliases={"rc3-main": "standard"}), "t")
    assert out["composition"] == "standard" and out["cases"] == {"x": ["b"]}


def test_default_still_means_the_profile_s_own_and_unknown_is_refused(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "default")
    out = profiles.apply_composition(_toy(default_composition="standard"), "t")
    assert out["composition"] == "default" and out["cases"] == {"x": ["a"]}
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc9")
    with pytest.raises(ConfigError, match="unknown composition"):
        profiles.apply_composition(_toy(composition_aliases={"rc3-main": "standard"}), "t")


def test_a_bad_alias_or_default_is_refused(monkeypatch):
    monkeypatch.delenv(profiles.COMPOSITION_ENV, raising=False)
    with pytest.raises(ConfigError):
        profiles.apply_composition(_toy(default_composition="nope"), "t")
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "old")
    with pytest.raises(ConfigError):
        profiles.apply_composition(_toy(composition_aliases={"old": "nope"}), "t")
