"""A composition may carry a ``main_nsfw`` block (deep-merged onto the profile's; null deletes, lists replace),
the ``nsfw-screen-1`` rig screen
(A 24 pieces, K 6 dealt hands (the run's deal), Lockout 0.5, no B, no Coding / Chat Main items)."""
import copy

import pytest

from crucibleforge import chains, config, profiles
from crucibleforge.config import ConfigError

CFG = config.load_config(config.EXAMPLE_CONFIG_PATH)


def _load(monkeypatch, name):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, name)
    return profiles.load_profile("bench45", CFG)


def test_deep_merge_rules():
    base = {"a": {"x": 1, "y": [1, 2], "z": {"k": 1}}, "b": 5, "gone": 1}
    keep = copy.deepcopy(base)
    out = profiles._deep_merge(base, {"a": {"y": [9], "z": {"j": 2}, "x": None}, "gone": None,
                                      "new": {"q": None, "r": 1}})
    assert out == {"a": {"y": [9], "z": {"k": 1, "j": 2}}, "b": 5, "new": {"r": 1}}
    assert base == keep                                           # the inputs never change


def test_composition_without_main_nsfw_leaves_the_block(monkeypatch):
    base = _load(monkeypatch, "rc1-night")
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "default")     # the base (unset = `standard` since the cut)
    assert base["main_nsfw"] == profiles.load_profile("bench45", CFG)["main_nsfw"]


def test_main_nsfw_must_be_a_mapping():
    prof = {"compositions": {"x": {"main_nsfw": [1]}}}
    import os
    was = os.environ.get(profiles.COMPOSITION_ENV)
    os.environ[profiles.COMPOSITION_ENV] = "x"
    try:
        with pytest.raises(ConfigError, match="main_nsfw must be a mapping"):
            profiles.apply_composition(prof, "t")
    finally:
        # restore, never delete: an unset variable is `standard` since the cut (it leaked into later modules)
        if was is None:
            del os.environ[profiles.COMPOSITION_ENV]
        else:
            os.environ[profiles.COMPOSITION_ENV] = was


def test_screen_composition_sizes(monkeypatch):
    p = _load(monkeypatch, "nsfw-screen-1")
    assert p["composition"] == "nsfw-screen-1"
    nsfw = p["scores"]["nsfw"]["parts"]
    assert {k: v["max"] for k, v in nsfw.items() if v.get("max")} == {"a": 4.5, "k": 1.0, "lockout": 0.5}
    assert not [k for k in nsfw if k not in ("a", "k", "lockout")]
    assert set(p["chains"]) == {"nsfw/a", "nsfw/k"}
    assert p["flags"]["lockout"]["scored"] is True
    for sec in ("work", "chat"):                                  # no Main item of the other sections
        assert not p["scores"][sec]["parts"]
    assert set(p["cases"]) == {"perf", "nsfw"} and p["cases"]["nsfw"] == []
    assert not profiles.core_generated_cases(p)
    cs = chains.chain_cases(p)
    by = {}
    for c in cs:
        by.setdefault(c["chain_spec"]["part"], []).append(c["chain_spec"])
    assert len(by["nsfw/a"]) == 24 and len(by["nsfw/k"]) == 6 and set(by) == {"nsfw/a", "nsfw/k"}
    assert sorted(s["rungs"][0]["level"] for s in by["nsfw/a"]) == [1] * 12 + [2] * 12
    assert {s["deal_of"] for s in by["nsfw/k"]} == {6}            # the run's own 6-way deal
    # no card drop list on A, in the screen or the profile (owner 2026-11-14: docs/CONTENT-RULES.md only)
    assert "drop" not in profiles.load_profile("bench45", CFG)["main_nsfw"]["rc2"]["chains"]["nsfw/a"]["flat"][0]
    assert not any(r.get("exclude") for s in by["nsfw/a"] for r in s["rungs"])


def test_screen_leaves_the_default_profile_alone(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "default")     # the base (unset = `standard` since the cut)
    d = profiles.load_profile("bench45", CFG)
    assert d["main_nsfw"]["composition"] == "rc1"
    assert "b" in d["main_nsfw"]["rc2"]["parts"] and "nsfw/b" in d["main_nsfw"]["rc2"]["chains"]
