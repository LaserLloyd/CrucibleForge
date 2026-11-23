"""The realignment marks (calibration/MARKS-INDEX-45.md) are frozen by sha256: every listed file exists and hashes
to its recorded value, and the index carries its written-before sentence."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "calibration" / "MARKS-INDEX-45.md"
ROW = re.compile(r"^\| `(calibration/[^`]+)` \| `([0-9a-f]{64})` \|")


def _rows():
    return [m.groups() for line in INDEX.read_text(encoding="utf-8").splitlines() if (m := ROW.match(line))]


def test_index_sentence():
    assert "written 2026-11-19 before any launch row was read" in INDEX.read_text(encoding="utf-8")


def test_index_lists_files():
    rows = _rows()
    assert len(rows) >= 10
    names = {p for p, _ in rows}
    for need in ("segment-marks-45.json", "nsfw-classing-marks-45.json", "act-beat-marks-45.json",
                 "everyday-marks-45.json", "judge-marks-45.1-draft.json"):
        assert f"calibration/{need}" in names


def test_hashes_match():
    for path, sha in _rows():
        got = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        assert got == sha, f"{path}: {got} != {sha} (a marks file changed: add a dated addendum instead)"


def test_chat_scene_draft_promoted_unchanged():
    assert dict((p, s) for p, s in _rows())["calibration/judge-marks-45.1-draft.json"].startswith("86c28ffb")
