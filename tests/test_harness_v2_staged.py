"""Harness v2, WP0-G: a set marked ``"staged": true`` (a finished pack
waiting for the owner's approval) loads for explicit runs but never unlocks,
is never due, never scores and never reaches the board or MAXED."""
from __future__ import annotations

import argparse
import json
import os

import pytest

from crucibleforge import cli, config, ladder, report
from crucibleforge.profiles import load_profile

STD = ["MH16-digit-sum-power", "MH22-dual-base-palindrome", "CZ11-lex-triple-substring"]
STAGED_CASE = {"id": "ZZ-STAGED-1", "category": "reasoning", "prompt": "What is 2+2?",
               "grader": "exact", "expected": "4"}

@pytest.fixture(autouse=True)
def _restore_process_env():
    """_apply_process_switches writes os.environ directly: put the staged and
    cap switches back so no later test sees them."""
    import os
    from crucibleforge import ladder as _l, spend as _s
    keep = {k: os.environ.get(k) for k in (_l.STAGED_ENV, _s.CAP_ENV)}
    yield
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _s.reset()



@pytest.fixture
def staged_sets(tmp_path, monkeypatch):
    root = tmp_path / "ladder"
    d = root / "work" / "reasoning"
    d.mkdir(parents=True)
    (d / "set-1.json").write_text(json.dumps({"cases": STD, "level": 1}), encoding="utf-8")
    (d / "set-2.json").write_text(json.dumps({"cases": [STAGED_CASE], "level": 1,
                                              "staged": True}), encoding="utf-8")
    monkeypatch.setattr(ladder, "roots", lambda *a, **k: [root])
    monkeypatch.delenv(ladder.STAGED_ENV, raising=False)
    return root


def _prof():
    return load_profile("bench", {})


def test_is_staged_accepts_both_spellings():
    assert ladder.is_staged({"staged": True})
    assert ladder.is_staged({"status": "staged"})
    assert not ladder.is_staged({"staged": False})
    assert not ladder.is_staged({"status": "live"})
    assert not ladder.is_staged({})


def test_staged_set_is_hidden_from_discover_unless_included(staged_sets, monkeypatch, tmp_path):
    prof = _prof()
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "scratch")
    found = ladder.discover(prof)
    assert [s["n"] for s in found["work/reasoning"]] == [1]
    assert "ZZ-STAGED-1" not in ladder.case_levels(prof)
    both = ladder.discover(prof, staged=True)
    assert [s["n"] for s in both["work/reasoning"]] == [1, 2]
    monkeypatch.setenv(ladder.STAGED_ENV, "1")
    assert [s["n"] for s in ladder.discover(prof)["work/reasoning"]] == [1, 2]


def test_staged_set_never_due_or_scored(staged_sets):
    prof = _prof()
    seq = ladder.discover(prof)["work/reasoning"]
    # standard and set 1 both maxed: a staged level-1 sibling still never opens
    assert ladder.due(0.95, {}, seq, 0.9) == [1]
    st = ladder.states(0.95, {1: 1.0}, seq, 0.9)
    assert set(st) == {1}
    assert ladder.points(0.95, 5, {1: 1.0, 2: 1.0}, seq, 0.9) == pytest.approx(0.95 * 5 + 1.0)


def test_staged_rows_never_reach_the_board(staged_sets):
    """A results dir that somehow holds rows of a staged pack question: the
    scorecard has no set for it, so the points, the unrun count and MAXED
    ignore it."""
    prof = _prof()
    rows = [{"case_id": "ZZ-STAGED-1", "category": "reasoning", "grade": "pass", "repeat": 1},
            {"case_id": STD[0], "category": "reasoning", "grade": "pass", "repeat": 1},
            {"case_id": "SOME-STANDARD", "category": "reasoning", "grade": "pass", "repeat": 1}]
    by_t = report.tier_rows(prof, rows, None)
    ids = {r["case_id"] for t in by_t.values() for r in t}
    assert "ZZ-STAGED-1" not in ids                      # neither standard nor a set
    assert [r["case_id"] for r in by_t[2]] == [STD[0]]
    assert "work/reasoning#2" not in {f"{k}#{n}" for k, n in report._ladder_levels(prof).values()}


def test_inline_cases_mark_staged_questions(staged_sets):
    ids = {c["id"]: c for c in ladder.inline_cases()}
    assert ids["ZZ-STAGED-1"]["ladder_staged"] is True


def test_pack_overview_lists_staged_without_bonus(staged_sets):
    ov = ladder.pack_overview(_prof(), None, dirs=[staged_sets])
    part = next(p for p in ov["parts"] if p["key"] == "work/reasoning")
    assert [s["status"] for s in part["sets"]] == ["live", "staged"]
    assert part["bonus_points"] == 1 and part["staged"] == 1
    assert "STAGED" in ladder.render_pack_overview(ov)


def test_verify_pack_notes_staged_and_rejects_bad_flag(tmp_path):
    d = tmp_path / "work" / "reasoning"
    d.mkdir(parents=True)
    f = d / "set-3.json"
    f.write_text(json.dumps({"cases": [dict(STAGED_CASE, id="ZZ-STAGED-3")], "staged": "yes"}), encoding="utf-8")
    problems, notes = ladder.verify_pack(f)
    assert any("staged 'yes'" in p for p in problems)
    f.write_text(json.dumps({"cases": [dict(STAGED_CASE, id="ZZ-STAGED-3")], "staged": True}), encoding="utf-8")
    problems, notes = ladder.verify_pack(f)
    assert any(n.startswith("staged") for n in notes)
    assert not any("staged" in p for p in problems)


def test_include_staged_refuses_the_board_results_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(ladder.STAGED_ENV, raising=False)
    monkeypatch.setattr(config, "RESULTS_DIR", config.ROOT_DIR / "results")
    with pytest.raises(SystemExit, match="--results"):
        cli._apply_process_switches(argparse.Namespace(include_staged=True, spend_cap_usd=None))
    assert not ladder.include_staged()
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "scratch")
    cli._apply_process_switches(argparse.Namespace(include_staged=True, spend_cap_usd=None))
    assert ladder.include_staged()
    os.environ.pop(ladder.STAGED_ENV, None)


def test_run_refuses_an_explicit_staged_case_without_the_flag(monkeypatch):
    monkeypatch.delenv(ladder.STAGED_ENV, raising=False)
    cases = [dict(STAGED_CASE, ladder_staged=True), {"id": "A", "category": "reasoning"}]
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda args, cfg: (cfg, cases, {}))
    monkeypatch.setattr(cli, "resolve_models", lambda cfg, m: [{"name": "m"}])
    args = argparse.Namespace(cmd="run", models="m", categories=None, difficulty=None,
                              cases="ZZ-STAGED-1", fresh=False, smoke=False, profile=None)
    monkeypatch.setattr(cli, "_refuse_fresh_with_filter", lambda a: None)
    with pytest.raises(SystemExit, match="staged pack"):
        cli.cmd_run(args, {})


def test_env_switch_cannot_bypass_the_board_results_guard(monkeypatch, tmp_path):
    """Fix pass S3: CRUCIBLEFORGE_INCLUDE_STAGED=1 without the flag is held to
    the same results-dir rule, for the config-relative results dir too."""
    monkeypatch.setenv(ladder.STAGED_ENV, "1")
    monkeypatch.setattr(config, "RESULTS_DIR", config.ROOT_DIR / "results")
    with pytest.raises(SystemExit, match="--results"):
        cli._apply_process_switches(argparse.Namespace(include_staged=False, spend_cap_usd=None))
    assert not ladder.include_staged()
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "scratch")
    monkeypatch.setenv(ladder.STAGED_ENV, "1")
    cli._apply_process_switches(argparse.Namespace(include_staged=False, spend_cap_usd=None))
    assert ladder.include_staged()


def _switch(monkeypatch):
    monkeypatch.setenv(ladder.STAGED_ENV, "1")
    return argparse.Namespace(include_staged=False, spend_cap_usd=None)


def test_a_configured_live_destination_is_refused_by_path_not_by_name(monkeypatch, tmp_path):
    """GPT round 2 #8: /srv/.../production-data is the live board because the
    configuration says so, whatever the directory is called; symlinks resolve."""
    live = tmp_path / "production-data"
    live.mkdir()
    link = tmp_path / "innocent"
    link.symlink_to(live)
    monkeypatch.setenv(config.LIVE_ENV, str(live))
    for path in (live, link):
        monkeypatch.setattr(config, "RESULTS_DIR", path)
        with pytest.raises(SystemExit, match="--results"):
            cli._apply_process_switches(_switch(monkeypatch))
        assert not ladder.include_staged()
    # a config the process loaded points at its own results dir: live too
    cfgdir = tmp_path / "cfg"
    cfgdir.mkdir()
    monkeypatch.setattr(config, "_CONFIG_RESULTS", [cfgdir / "elsewhere"])
    monkeypatch.setattr(config, "RESULTS_DIR", cfgdir / "elsewhere")
    with pytest.raises(SystemExit, match="--results"):
        cli._apply_process_switches(_switch(monkeypatch))


def test_a_dir_with_the_live_board_signature_needs_the_scratch_marker(monkeypatch, tmp_path):
    d = tmp_path / "copy-of-the-board"
    d.mkdir()
    (d / "report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "RESULTS_DIR", d)
    with pytest.raises(SystemExit, match="--results"):
        cli._apply_process_switches(_switch(monkeypatch))
    (d / config.SCRATCH_MARKER).write_text("", encoding="utf-8")
    cli._apply_process_switches(_switch(monkeypatch))
    assert ladder.include_staged()
    # meta / transcripts files are the signature too
    e = tmp_path / "other"
    e.mkdir()
    (e / "meta_m.json").write_text("{}", encoding="utf-8")
    assert config.looks_like_live_board(e) and not config.looks_like_live_board(d)
