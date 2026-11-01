"""Suite 3.9 runner: `all --plan` shows the load rounds, the budgets, the
ladder sets and a labelled wall-time estimate (crucibleforge/estimate.py)."""
from __future__ import annotations

import json

import pytest
import yaml

from crucibleforge import cli, estimate, ladder, providers, runner


@pytest.fixture(autouse=True)
def _no_models(monkeypatch):
    providers.clear_cache()
    monkeypatch.setattr(runner, "run_models", lambda *a, **k: pytest.fail("ran a model"))
    yield
    providers.clear_cache()


def _cfg():
    return {"models": [
        {"name": "rig", "model_id": "a/b", "provider": "studioforge", "context_length": 32768},
        {"name": "api", "model_id": "M", "provider": "minimax", "context_length": 1000000}],
        "providers": {"studioforge": {"type": "studioforge", "base_url": "http://s.invalid/v1"},
                      "minimax": {"type": "openai", "base_url": "http://m.invalid/v1"}},
        "defaults": {"context_length": 32768}, "judge": {"candidates": [], "samples": 3}}


def _plan(capsys, cfg=None, models="rig,api"):
    args = cli.argparse.Namespace(models=models, profile=None, judge=None, smoke=False,
                                  plan=True)
    assert cli.cmd_all(args, cfg or _cfg()) == 0
    return capsys.readouterr().out


def test_plan_shows_the_two_rig_rounds_and_one_api_round(capsys):
    out = _plan(capsys)
    rig, api = out.split("== api")
    # 4.0 lite: run_plan.split_rig_rounds false — ONE load at the bank window
    assert "round B" not in rig and "round all — every row" in rig
    assert "loaded at ctx 73728 (~4 slots)" in rig
    assert "65536 (bank)" in rig and "CB-C5-predict-pyhouse" in rig
    assert "TZ16-pickup-dst-delay" in rig and "x1  budget 16384" in rig  # 4.0: 1 repeat
    assert "CL3-paragraph-wrap" in rig and "CL1-span-parse" not in rig
    assert "if thinking" not in out                                 # one budget (S8)
    assert "one round — hosted API (nothing loaded)" in api and "8 requests at once" in api
    assert "round A" not in api
    for part in (rig, api):
        assert "estimate (not a measurement" in part and "judged-row equivalents at 3 samples, per-row sample policy" in part
    assert "no model, provider or judge was called; times are estimates" in out


def test_plan_lists_the_ladder_sets_with_their_rule_and_an_upper_bound(capsys, tmp_path,
                                                                       monkeypatch):
    d = tmp_path / "work" / "reasoning"
    d.mkdir(parents=True)
    (d / "set-1.json").write_text(json.dumps({
        "cases": ["MH16-digit-sum-power", "MH22-dual-base-palindrome", "CZ11-lex-triple-substring"],
        "budget": 65536, "level": 1}), encoding="utf-8")
    monkeypatch.setattr(ladder, "roots", lambda: [tmp_path])
    rig = _plan(capsys, models="rig")
    assert ("work/reasoning set 1 (level 1): 3 cases, 3 rows, budget 65536, loaded at ctx "
            "73728 — runs if >= 90% on standard") in rig
    assert "MH22-dual-base-palindrome" in rig and "65536 (ladder)" in rig
    assert "estimate if every set unlocks: +" in rig


def test_plan_without_ladder_sets_says_so(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(ladder, "roots", lambda: [tmp_path])     # the repo ships packs
    assert "ladder: no sets yet" in _plan(capsys, models="rig")


def test_the_estimate_formula():
    assert estimate.rig_slots(40960) == 8 and estimate.rig_slots(73728) == 4
    assert estimate.lpt([10, 10, 10, 10], 2) == 20 and estimate.lpt([30, 5, 5], 8) == 30
    assert estimate.generation_min([600] * 8, 8) == pytest.approx(12.0)   # 10 min x 1.2
    assert estimate.judge_min(0) == 0
    assert estimate.judge_min(10, samples=3) == pytest.approx(1.5 + 14.0)
    assert estimate.judge_min(10, samples=1) == pytest.approx(1.5 + 14.0 / 3)


def test_the_new_flags_parse(tmp_path, capsys):
    p = tmp_path / "models.yaml"
    p.write_text(yaml.safe_dump(_cfg()), encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        cli.main(["--config", str(p), "--results", str(tmp_path / "r"), "all", "--plan",
                  "--models", "api", "--escalate", "--no-escalate", "--timing", "--no-ladder",
                  "--no-tiers"])
    assert ei.value.code == 0
    assert "one round — hosted API" in capsys.readouterr().out
