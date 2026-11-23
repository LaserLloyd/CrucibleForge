"""Stamp-2 fix pass, findings 3 and 4: b-packs reads anchor folders; board-step signatures include anchors."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import release45 as R  # noqa: E402
from test_release45_packs import Sim, mkfolder, pack_row  # noqa: E402,F401
from test_release45_packs import _no_env_preflight  # noqa: E402,F401


def _args(tmp, a, *extra):
    return ["--folders", str(tmp / "r1"), "--board", str(tmp / "board45"), "--anchors", str(a), *extra]


def test_b_packs_plans_and_reads_an_anchor_with_pack_rows(tmp_path, capsys):
    mkfolder(tmp_path, "r1", "model-a", [pack_row()])
    a = mkfolder(tmp_path, "an", "claude-sonnet-x", [pack_row()])
    sim = Sim(hosted=True)
    assert R.main(_args(tmp_path, a, "--only", "b-packs", "--dry-run"), runner=sim.runner, capturer=sim.capturer) == 0
    assert "[b-packs] an" in capsys.readouterr().out
    sim = Sim(hosted=True)
    rc = R.main(_args(tmp_path, a, "--only", "b-packs", "--pack-cap-usd", "2"), runner=sim.runner, capturer=sim.capturer)
    assert rc == 0
    assert any(str(a) in c for c in sim.cmds if "read_chat45.py" in c[1])      # the anchor was read
    assert list(tmp_path.glob("an/.release45/b-packs.json"))


def test_board_signature_changes_when_an_anchor_folder_is_replaced(tmp_path):
    mkfolder(tmp_path, "r1", "model-a", [pack_row()])
    a = mkfolder(tmp_path, "an", "claude-sonnet-x", [pack_row()])
    sigs = []
    for body in ("hello", "a much longer replacement answer"):
        (a / "transcripts_claude-sonnet-x.jsonl").write_text(json.dumps(pack_row(resp=body)) + "\n", encoding="utf-8")
        rel = R.Release.__new__(R.Release)
        rel.folders, rel.anchors, rel.board = [tmp_path / "r1"], [a], tmp_path / "board45"
        sigs.append(rel.sig_for("c-packmarks", rel.board))
    assert sigs[0] != sigs[1]
