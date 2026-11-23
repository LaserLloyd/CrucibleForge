"""Fix pass B: fix-row stamp coverage (S4) and a missing sandbox (S5)."""
import hashlib
from pathlib import Path

from crucibleforge import fixrows45 as FR
from crucibleforge import graders


def test_s4_checker_stamp_covers_the_helper_files():
    here = Path(FR.__file__).resolve().parent
    for f in ("fixcheck.py", "fixspecs.py", "codebank.py", "gen/_patchbase.py", "graders.py"):
        assert f in FR._FILES, f
        assert (here / f).is_file()
    FR.checker_stamp.cache_clear()
    base = FR.checker_stamp()
    h = hashlib.sha256()
    for f in FR._FILES:
        h.update(f.encode() + b"\0" + (here / f).read_bytes() + b"\0")
    assert base == h.hexdigest()[:12]


def test_s5_no_sandbox_row_carries_unavailable_and_never_counts(monkeypatch):
    from crucibleforge import fixcheck

    def boom(*a, **k):
        raise graders.SandboxUnavailable("no bwrap")
    monkeypatch.setattr(fixcheck, "grade_answer", boom)
    case = {"id": "PATCHFIX-core-7-1", "grader_config": {}}
    v = FR.reply_verdict(case, "some reply", finish_reason="stop", completion_tokens=10, sent=1000)
    assert v == {"unavailable": True}
    rows = [{"case_id": "PATCHFIX-core-7-1", "repeat": 1, "fixcheck": v, "ts": "2026-11-19T10:00:00"}]
    cs = FR.case_scores(rows, ["PATCHFIX-core-7-1"])["cases"]["PATCHFIX-core-7-1"]
    assert cs["scores"] == [] and cs["old_grader"] == 0
    assert cs["reason"] == "pending: fix rows not graded: no sandbox"


def test_n12_plan_reader_line_unnamed_and_hosted(monkeypatch):
    # Part 1 (2026-11-21): the plan names the run's judge (local, free), never a reader or a hosted price
    from crucibleforge import classing45 as C
    from crucibleforge import cli

    def boom(*a, **k):
        raise AssertionError("the plan must not pick readers")
    monkeypatch.setattr(C, "pick_readers", boom)
    prof = {"judge": {"provider": "studioforge", "model_id": "google/gemma-4-31B-it-Q8_0"}}
    line = cli.plan_reader_line(100, "qwen", prof)
    assert "at most 100 calls by google/gemma-4-31B-it-Q8_0" in line and "(local, free)" in line
    assert "hosted" not in line and "$" not in line
    assert "same family" in cli.plan_reader_line(100, "google", prof)
    assert "no units" in cli.plan_reader_line(0, "qwen", prof)
