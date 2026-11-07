"""`crucibleforge pack list` (docs/PACKS.md): the public optional packs per
sub-score, which board models maxed each part, --json, and the guide's links."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from crucibleforge import cli, ladder, profiles, report
from test_points_board_39 import _set_file, _setup

REPO = Path(__file__).resolve().parent.parent


def _two_sets(env):
    q = {"id": "PL-Q1", "category": "coding", "difficulty": "hard", "max_tokens": 512,
         "prompt": "x", "grader": "python_exec"}
    _set_file(env, "work/code", 1, [q, {**q, "id": "PL-Q2"}, {**q, "id": "PL-Q3"}],
              note="First pack. Second sentence.")
    _set_file(env, "work/code", 2, [{**q, "id": "PL-Q4"}], level=2, allow_small=True,
              allow_small_reason="fixture")
    _set_file(env, "work/reasoning", 1, [{**q, "id": "PL-Q5"}], draft=True, allow_small=True)


def test_overview_counts_sets_cases_and_maxed(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch)
    _two_sets(env)
    prof = profiles.load_profile("bench", env.cfg)
    ov = ladder.pack_overview(prof, {"work/code": ["m1"]}, dirs=[env.ladder], minutes=False)
    by = {p["key"]: p for p in ov["parts"]}
    code = by["work/code"]
    assert [s["n"] for s in code["sets"]] == [1, 2]
    assert [s["cases"] for s in code["sets"]] == [3, 1]
    assert code["bonus_points"] == 2 and code["maxed_by"] == ["m1"] and code["needs_pack"]
    assert code["sets"][0]["note"] == "First pack"
    # a draft is not a pack yet; a part with none is listed with no sets
    assert by["work/reasoning"]["sets"] == [] and by["work/reasoning"]["drafts"] == 1
    assert not by["work/reasoning"]["needs_pack"]
    assert ov["unlock"] == 0.9
    text = ladder.render_pack_overview(ov)
    assert "MAXED by m1: next pack is set 3" in text and "docs/PACKS.md" in text


def test_overview_without_board_says_so(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch)
    _two_sets(env)
    prof = profiles.load_profile("bench", env.cfg)
    ov = ladder.pack_overview(prof, None, dirs=[env.ladder], minutes=False)
    assert not ov["board_read"] and all(p["maxed_by"] is None for p in ov["parts"])
    assert "board not read" in ladder.render_pack_overview(ov)


def test_cmd_pack_list_json_uses_calibration(tmp_path, monkeypatch, capsys):
    env = _setup(tmp_path, monkeypatch)
    _two_sets(env)
    rows = [{"key": "work/code", "maxed_by": ["m1", "m2"]}, {"key": "work/reasoning", "maxed_by": []}]
    monkeypatch.setattr(report, "calibration", lambda cfg, ref=None: {"rows": rows})
    real = ladder.pack_overview
    monkeypatch.setattr(ladder, "pack_overview",
                        lambda p, m: real(p, m, dirs=[env.ladder], minutes=False))
    rc = cli.cmd_pack(SimpleNamespace(action="list", json=True, no_board=False), env.cfg)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["board_read"]
    code = next(p for p in out["parts"] if p["key"] == "work/code")
    assert code["maxed_by"] == ["m1", "m2"] and len(code["sets"]) == 2


def test_cmd_pack_list_reads_report_json_not_calibration(tmp_path, monkeypatch, capsys):
    """The default path reads results/report.json (seconds); calibration() rebuilds every model (minutes)
    and is only the --recompute / no-report fallback."""
    env = _setup(tmp_path, monkeypatch)
    _two_sets(env)
    rj = report.report_json_path()
    rj.parent.mkdir(parents=True, exist_ok=True)
    rj.write_text(json.dumps({"generated": "x", "models": {
        "m1": {"scorecard": {"ladder": {"work": {"code": {"maxed": True}, "reasoning": {"maxed": False}}}}},
        "m2": {"scorecard": {"ladder": {"work": {"code": {"maxed": True}}}}},
        "m3": {"scorecard": {}}}}), encoding="utf-8")

    def boom(cfg, ref=None):
        raise AssertionError("calibration must not run when report.json exists")
    monkeypatch.setattr(report, "calibration", boom)
    real = ladder.pack_overview
    monkeypatch.setattr(ladder, "pack_overview",
                        lambda p, m: real(p, m, dirs=[env.ladder], minutes=False))
    rc = cli.cmd_pack(SimpleNamespace(action="list", json=True, no_board=False), env.cfg)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["board_read"]
    assert next(p for p in out["parts"] if p["key"] == "work/code")["maxed_by"] == ["m1", "m2"]
    # --recompute goes the slow way
    monkeypatch.setattr(report, "calibration", lambda cfg, ref=None: {"rows": [{"key": "work/code", "maxed_by": ["z"]}]})
    cli.cmd_pack(SimpleNamespace(action="list", json=True, no_board=False, recompute=True), env.cfg)
    out = json.loads(capsys.readouterr().out)
    assert next(p for p in out["parts"] if p["key"] == "work/code")["maxed_by"] == ["z"]


def test_cmd_pack_list_survives_unreadable_board(tmp_path, monkeypatch, capsys):
    env = _setup(tmp_path, monkeypatch)
    _two_sets(env)

    def boom(cfg, ref=None):
        raise ValueError("no board")
    monkeypatch.setattr(report, "calibration", boom)
    rc = cli.cmd_pack(SimpleNamespace(action="list", json=False, no_board=False), env.cfg)
    assert rc == 0 and "board not read" in capsys.readouterr().out


def test_real_packs_listed_and_guide_linked():
    """Every shipped public pack appears in the guide and the README points to it."""
    guide = (REPO / "docs" / "PACKS.md").read_text(encoding="utf-8")
    rows = [ln for ln in guide.splitlines() if ln.startswith("| `")]
    for f in (REPO / "cases" / "ladder").glob("*/*/set-*.json"):
        key = f"{f.parent.parent.name}/{f.parent.name}"
        n_cases = len(json.loads(f.read_text(encoding="utf-8"))["cases"])
        assert any(f"`{key}`" in ln and f"| {f.stem} | {n_cases} |" in ln for ln in rows), f
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "docs/PACKS.md" in readme and "crucibleforge pack list" in readme
