"""Conformance with INTENT-45 (owner 2026-11-21; SPEC-BUILD-45 item 14): the score tree and its maxima, NSFW+ /20 =
NSFW + Uncensored, no hosted model in any grading or checking role of the profile, the accuracy thresholds, and no
code path that prints "held: marks not met". This file is how the intent is enforced in the build."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from crucibleforge import nsfwplus45 as N
from crucibleforge import segments45 as S
from crucibleforge import spend

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]

ROOT = Path(__file__).resolve().parents[1]
RAW = yaml.safe_load((ROOT / "profiles" / "bench45.yaml").read_text(encoding="utf-8"))
PROF = {**RAW, **RAW["compositions"][RAW.get("default_composition") or "standard"]}


def test_score_tree_and_maxima():
    t = S.tree(PROF)
    assert {k: v["max"] for k, v in t["code"].items() if isinstance(v, dict)} == \
        {"writes": 4, "fixes": 3, "tools": 2, "reads": 1}
    assert {k: v["max"] for k, v in t["chat"].items() if isinstance(v, dict)} == \
        {"character": 4, "remembers": 3.5, "cowrites": 1.5, "everyday": 1}
    assert t["overall"] == ["code", "chat"]                       # Overall /20 = Code + Chat; NSFW+ is apart
    nums = PROF["nsfw_plus"]["numbers"]
    assert nums["nsfw"]["of"] == 10 and nums["uncensored"]["of"] == 10
    assert sum(v["max"] for k, v in nums["nsfw"].items() if isinstance(v, dict) and "max" in v) == 10
    assert sum(v["max"] for k, v in nums["uncensored"].items() if isinstance(v, dict) and "max" in v) == 10


def test_nsfw_plus_is_nsfw_plus_uncensored_of_20():
    t = N.total_of({"nsfw": {"value": 6.25, "partial": 6.25}, "uncensored": {"value": 7.5, "partial": 7.5}})
    assert t["of"] == 20 and t["value"] == pytest.approx(13.75)
    half = N.total_of({"nsfw": {"value": None, "partial": 3.0}, "uncensored": {"value": 7.5, "partial": 7.5}})
    assert half["value"] is None                                  # never a number with a half missing


_ROLE_KEYS = {"judge", "readers", "reader", "second_reader", "grader", "checker", "hosted_fallback", "local", "set"}
_HOSTED_WORDS = ("deepseek", "minimax", "moonshot", "kimi", "openrouter", "openai", "gpt-", "grok", "xai",
                 "claude", "anthropic", "gemini")


def _role_values(node, under_role=False):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "never_family":                               # families excluded from reading, not roles
                continue
            yield from _role_values(v, under_role or k in _ROLE_KEYS)
    elif isinstance(node, list):
        for v in node:
            yield from _role_values(v, under_role)
    elif under_role and isinstance(node, str):
        yield node


def test_no_hosted_model_in_any_grading_or_checking_role():
    j = PROF["judge"]
    assert not spend.is_hosted(j["provider"])
    vals = [v.lower() for v in _role_values(PROF)]
    named = [v for v in vals if v.startswith(("rh-",)) or (any(w in v for w in _HOSTED_WORDS) and "/" not in v)]
    assert named == [], named
    assert PROF["nsfw_plus"]["classing"]["readers"]["hosted_fallback"] == []
    assert PROF["rplong_score"]["definition"] == "judge_only"     # no second reader in Chat


def test_accuracy_thresholds_present():
    assert RAW["intent"]["accuracy_target_pct"] == 2.5
    assert RAW["intent"]["accuracy_limit_pct"] == 5


def test_no_code_path_prints_held_marks_not_met():
    hits = [p.name for p in (ROOT / "crucibleforge").rglob("*.py")
            if "held: marks not met" in p.read_text(encoding="utf-8")]
    hits += [p.name for p in (ROOT / "scripts").rglob("*.py") if "held: marks not met" in p.read_text(encoding="utf-8")]
    assert hits == []
