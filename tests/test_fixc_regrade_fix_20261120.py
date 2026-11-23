"""Fix pass C (A9): stored single-turn fix replies re-grade under the current checker; bug-hunt agent rows say they
cannot be re-graded."""
import importlib.util
from pathlib import Path

from crucibleforge import fixrows45 as FR

_spec = importlib.util.spec_from_file_location(
    "regrade_fix45", Path(__file__).resolve().parents[1] / "scripts" / "regrade_fix45.py")
RG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RG)


def test_stale_one_shot_rows_regrade_and_agent_rows_are_reported(monkeypatch):
    calls = []

    def fake(case, reply, **kw):
        calls.append(case["id"])
        return {"score": 1.0, "grade": "pass", "checker": "x", "stamp": FR.checker_stamp(), "cut": False}
    monkeypatch.setattr(FR, "reply_verdict", fake)
    rows = [{"case_id": "PATCHFIX-core-7-1", "response": "x", "grader_config": {"k": 1},
             "fixcheck": {"score": 0.0, "stamp": "old"}},
            {"case_id": "CB-F1-larder-bughunt", "fixcheck": {"score": 0.5, "stamp": "old"}},
            {"case_id": "PATCHFIX-core-7-2", "fixcheck": {"score": 1.0, "stamp": FR.checker_stamp()}},
            {"case_id": "c", "response": "y"}]
    new, n = RG.regrade_rows(rows)
    assert calls == ["PATCHFIX-core-7-1"] and new[0]["fixcheck"]["score"] == 1.0
    assert n["regraded"] == 1 and n["changed"] == 1 and n["agent_not_regradable"] == 1 and n["current"] == 1
    assert new[1] is rows[1] and new[3] is rows[3]


def test_main_prints_the_agent_line(tmp_path, monkeypatch, capsys):
    import json
    (tmp_path / "transcripts_m.jsonl").write_text(json.dumps(
        {"case_id": "CB-F1-larder-bughunt", "fixcheck": {"score": 0.5, "stamp": "old"}}) + "\n", encoding="utf-8")
    assert RG.main(["--results", str(tmp_path)]) == 0
    assert "cannot be re-graded" in capsys.readouterr().out
