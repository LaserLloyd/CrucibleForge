"""Fix pass C (B4, B6): release45 --anchors (validation-only rows used by the marks, never ranked, listed apart with
their Everyday share) and --admission (stored C1 / C7 results seeded, refused unless the reader and prompt are
indexed)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import release45 as R  # noqa: E402
from test_release45 import Sim, mkfolder  # noqa: E402

from crucibleforge import classing45 as C, marksindex45, report  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_env_preflight(monkeypatch):
    # the env preflight (pilot 2, 6fd3e67) has its own tests in test_release45; these simulated runs need no config
    monkeypatch.setattr(R.Release, "preflight_env", lambda self: None)


def _run(tmp, monkeypatch, extra, sim=None):
    sim = sim or Sim(monkeypatch)
    rc = R.main(["--folders", str(tmp / "runs" / "r1"), "--board", str(tmp / "board45"), *extra],
                runner=sim.runner, capturer=sim.capturer)
    return rc, sim


def test_anchor_rows_feed_the_marks_and_never_the_board(tmp_path, monkeypatch):
    (tmp_path / "runs").mkdir()
    (tmp_path / "anchors").mkdir()
    mkfolder(tmp_path / "runs", "r1", "qwen3.8-27b-q8-ref")
    mkfolder(tmp_path / "anchors", "dam", "split-untied-31b-q4")
    rc, sim = _run(tmp_path, monkeypatch, ["--anchors", str(tmp_path / "anchors" / "dam"), "--only",
                                       "c-assemble,c-assistant,c-marks"])   # mark steps by name (item 13)
    assert rc == 0, sim.cmds
    marks_cmd = next(c for c in sim.cmds if c[1].endswith("check_marks45.py"))
    assert str(tmp_path / "anchors" / "dam") in marks_cmd
    assist = next(c for c in sim.cmds if c[1].endswith("check_assistant_marks.py"))
    assert str(tmp_path / "anchors" / "dam") in assist
    assert not list((tmp_path / "board45").glob("transcripts_split*"))           # never assembled, never ranked
    anc = json.loads((tmp_path / "board45" / "anchors45.json").read_text(encoding="utf-8"))["anchors"]
    assert anc[0]["label"] == "split-untied-31b-q4"
    assert report.anchors_lines(tmp_path / "board45") == [
        "split-untied-31b-q4: Everyday share not measured"]


def _admission(tmp, model, sha):
    p = tmp / "adm.json"
    p.write_text(json.dumps({"classing": {"C1_constructed": {"per_reader": {"r": {
        "model": model, "family": "deepseek", "prompt_sha": sha, "status": "pass"}}}}}), encoding="utf-8")
    return p


def _c1_sha():
    return next(sha for path, sha in marksindex45.rows() if Path(path).name.startswith("nsfw-classing-prompt-45"))


def test_admission_seeds_every_folder(tmp_path, monkeypatch):
    (tmp_path / "runs").mkdir()
    mkfolder(tmp_path / "runs", "r1", "m")
    model = next(d["model_id"] for d in C.named_readers().values())
    adm = _admission(tmp_path, model, _c1_sha())
    rc, sim = _run(tmp_path, monkeypatch, ["--admission", str(adm), "--only", "c-assemble,d-report"])
    assert rc == 0
    for d in (tmp_path / "runs" / "r1", tmp_path / "board45"):
        c1 = C.load_c1(d)
        assert c1 and c1[0]["model"] == model


def test_admission_refuses_an_unnamed_reader_or_an_unindexed_prompt(tmp_path, monkeypatch, capsys):
    (tmp_path / "runs").mkdir()
    mkfolder(tmp_path / "runs", "r1", "m")
    rc, _ = _run(tmp_path, monkeypatch, ["--admission", str(_admission(tmp_path, "grok-4", _c1_sha()))])
    assert rc == 2 and "not named by an indexed reader addendum" in capsys.readouterr().out
    model = next(d["model_id"] for d in C.named_readers().values())
    rc, _ = _run(tmp_path, monkeypatch, ["--admission", str(_admission(tmp_path, model, "0" * 64))])
    assert rc == 2 and "is not an indexed" in capsys.readouterr().out
    assert not (tmp_path / "runs" / "r1" / "marks45.json").exists()
