"""Stamp-2 G6a: check_rplong_marks45 reads the shared reader store, which also holds classing vote lines (no `part`)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_rplong_marks45 as CR  # noqa: E402
from crucibleforge import rplong_marks as RK  # noqa: E402


def test_classing_vote_lines_in_the_store_are_ignored(tmp_path, monkeypatch, capsys):
    store = tmp_path / "readers45"
    store.mkdir()
    vote = {"unit": "u1", "reader": "deepseek-v4-flash", "prompt_sha": "x", "vote": {"cls": "EXPLICIT"},
            "model": "qwen3.8-27b-q8-ref"}
    long_rec = {"part": "chat/j_rplong", "model": "qwen3.8-27b-q8-ref",
                "unit": "chat/j_rplong|RD1-marisol|1", "reader": "RH-kimi"}
    (store / "m-a.jsonl").write_text(json.dumps(vote) + "\n\n" + json.dumps(long_rec) + "\n" + json.dumps(vote) + "\n",
                                     encoding="utf-8")
    seen = {}

    def fake_check(recs, marks, **kw):
        seen["recs"] = list(recs)
        return {"pass": False, "measured": False, "reason": "stub", "fails": ["stub"], "groups": {}}
    monkeypatch.setattr(RK, "check", fake_check)
    monkeypatch.setattr(RK, "load_marks", lambda: {})
    rc = CR.main(["--results", str(tmp_path), "--no-write"])
    assert rc == 2
    assert seen["recs"] and all("part" in r for r in seen["recs"])
    assert not any("vote" in r for r in seen["recs"])
