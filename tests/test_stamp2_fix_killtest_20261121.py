"""Stamp-2 fix pass, finding 8: the kill test's tree copy leaves out only the root-level scratch dirs, so the
Work ladder (cases/ladder/work/) stays; it prints what it left out and what it removed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import killtest_margins45 as K  # noqa: E402


def _tree(tmp: Path) -> Path:
    t = tmp / "src"
    (t / "cases/ladder/work/c1_implied").mkdir(parents=True)
    (t / "cases/ladder/work/c1_implied/set-1.json").write_text(json.dumps({"item": "STANDARD"}), encoding="utf-8")
    (t / "cases/ladder/work/c1_implied/set-9.json").write_text(json.dumps({"item": "PATCHTOUGH-1"}), encoding="utf-8")
    (t / "cases/ladder/work/pk_x").mkdir()
    (t / "cases/ladder/work/pk_x/set-1.json").write_text("{}", encoding="utf-8")
    (t / "work/other-worktree").mkdir(parents=True)
    (t / "results").mkdir()
    (t / ".venv").mkdir()
    (t / "crucibleforge").mkdir()
    (t / "crucibleforge/__pycache__").mkdir()
    return t


def test_stripped_tree_keeps_the_work_ladder_and_leaves_out_only_root_scratch(tmp_path, monkeypatch, capsys):
    src = _tree(tmp_path)
    monkeypatch.setattr(K, "ROOT", src)
    dest = tmp_path / "dest"
    gone = K.make_stripped_tree(dest)
    assert (dest / "cases/ladder/work/c1_implied/set-1.json").is_file()      # the standard Work set stays
    assert sorted(gone) == ["cases/ladder/work/c1_implied/set-9.json", "cases/ladder/work/pk_x/set-1.json"]
    assert not (dest / "work").exists() and not (dest / "results").exists() and not (dest / ".venv").exists()
    assert not (dest / "crucibleforge/__pycache__").exists()
    out = capsys.readouterr().out
    assert "left out" in out and "work" in out and "results" in out
