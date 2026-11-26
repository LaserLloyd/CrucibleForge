"""4.5.1 forfeits (forfeit45.py): an owner-declared record turns one MISSING job (reason error, the provider never
answered) into a failed answer. Never automatic, refused unless the job qualifies, shown in the notes, carried onto
the combined board."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from crucibleforge import config, forfeit45, scoring45

ROOT = Path(__file__).resolve().parents[1]

CASE = "PATCHFIX-x-1"
DETAIL = "transport failure: no data event for 600s, only keep-alive comments"


def _err(**kw):
    r = {"case_id": CASE, "repeat": 1, "turn": None, "category": "coding", "grade": "error",
         "response": "", "finish_reason": "error", "error": DETAIL, "model_label": "m"}
    r.update(kw)
    return r


def _ok(case="OTHER-1"):
    return {"case_id": case, "repeat": 1, "turn": None, "category": "coding", "grade": "pass",
            "response": "fixed", "finish_reason": "stop", "score": 1.0}


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    forfeit45._CACHE.clear()
    return tmp_path


def _record(folder, model="m", case=CASE, repeat=1):
    (folder / forfeit45.FILENAME).write_text(json.dumps([{
        "model": model, "case_id": case, "repeat": repeat, "reason": "never answered",
        "declared": "2026-11-27T00:00:00+00:00",
        "attempts": [{"ts": f"2026-11-2{i}T00:00:00+00:00", "detail": DETAIL} for i in range(5)]}]),
        encoding="utf-8")


def _fail(r):
    from crucibleforge.report import _forfeit_failed
    return _forfeit_failed(r, None)


def _by_t(rows):
    return {1: rows, 2: [], 3: []}


def test_without_a_record_the_job_stays_missing(folder):
    rows = [_err(), _ok()]
    assert forfeit45.apply("m", rows, frozenset(), _fail) is rows
    _, missing = forfeit45.split_missing(_by_t(rows))
    assert missing == [{"case_id": CASE, "repeat": 1, "reason": "error"}]
    assert forfeit45.applied("m", _by_t(rows)) == []


def test_a_record_scores_the_job_as_a_failed_answer(folder):
    _record(folder)
    rows = [_err(), _ok()]
    out = forfeit45.apply("m", rows, frozenset(), _fail)
    row = out[0]
    assert row["grade"] == "fail" and row["forfeit"] is True
    assert rows[0]["grade"] == "error"                    # the loaded rows themselves are never changed
    kept, missing = forfeit45.split_missing(_by_t(out))
    assert missing == []                                  # no longer missing: the row is scored as a failed answer
    assert [r["case_id"] for r in kept[1]] == [CASE, "OTHER-1"]      # order kept
    assert forfeit45.applied("m", kept) == [{"case_id": CASE, "repeat": 1, "reason": "never answered",
                                             "attempts": 5, "declared": "2026-11-27T00:00:00+00:00"}]
    # every other job is still checked: a second errored job stays missing
    kept, missing = forfeit45.split_missing(_by_t(out + [_err(case_id="OTHER-2")]))
    assert missing == [{"case_id": "OTHER-2", "repeat": 1, "reason": "error"}]
    assert [r["case_id"] for r in kept[1]] == [CASE, "OTHER-1"]


def test_a_fix_unit_gets_the_checkers_verdict_for_an_empty_reply():
    from crucibleforge import fixrows45
    from crucibleforge.report import _forfeit_failed
    case = {"id": CASE, "generator": {"type": "patchfix"}, "grader_config": {"x": 1}}
    assert fixrows45.is_fix_case(case)
    r = _forfeit_failed(_err(), case)
    assert r["score"] == 0.0 and r["fixcheck"]["score"] == 0.0 and r["fixcheck"]["grade"] == "fail"
    assert r["fixcheck"]["stamp"] == fixrows45.checker_stamp()
    # the Fixes segment's scorer reads it as a scored zero, the stored error row as no verdict at all
    assert fixrows45.case_scores([r], [CASE])["cases"][CASE]["scores"] == [0.0]
    assert fixrows45.case_scores([_err()], [CASE])["cases"][CASE]["scores"] == []
    # a generated patch case is not in the static case set: the score tree's Fixes list names it instead
    r = _forfeit_failed(_err(), None, fix=True)
    assert fixrows45.case_scores([r], [CASE])["cases"][CASE]["scores"] == [0.0]
    assert "fixcheck" not in _forfeit_failed(_err(), None)


def test_the_record_is_keyed_by_model_and_repeat(folder):
    _record(folder, model="other")
    assert forfeit45.apply("m", [_err()], frozenset(), _fail)[0]["grade"] == "error"
    _record(folder, repeat=2)
    assert forfeit45.apply("m", [_err()], frozenset(), _fail)[0]["grade"] == "error"
    _record(folder, repeat="1")                           # a repeat read from a csv is the same job
    assert forfeit45.apply("m", [_err()], frozenset(), _fail)[0]["grade"] == "fail"


def test_a_later_answer_beats_the_record(folder):
    _record(folder)
    graded = {**_ok(CASE), "grade": "fail", "score": 0.4}
    rows = [graded]
    assert forfeit45.apply("m", rows, frozenset(), _fail) is rows


def test_a_job_missing_for_another_reason_is_not_forfeited(folder):
    _record(folder)
    empty = {**_ok(CASE), "grade": "fail", "response": "", "score": 0.0}   # a dropped stream: "empty"
    assert scoring45.job_missing_reason([empty]) == "empty"
    rows = [empty]
    assert forfeit45.apply("m", rows, frozenset(), _fail) is rows


def test_an_unreadable_file_applies_nothing(folder):
    (folder / forfeit45.FILENAME).write_text("{not json", encoding="utf-8")
    rows = [_err()]
    assert forfeit45.apply("m", rows, frozenset(), _fail) is rows


def test_the_note_is_one_plain_line():
    assert forfeit45.note([]) is None
    assert forfeit45.note([{"case_id": "PATCHFIX-core-4-1", "repeat": 1, "attempts": 5}]) == \
        "1 job counted as 0: PATCHFIX-core-4-1, the provider never answered in 5 tries"
    two = forfeit45.note([{"case_id": "A-1", "repeat": 1, "attempts": 3}, {"case_id": "B-1", "repeat": 2,
                                                                           "attempts": 4}])
    assert two == "2 jobs counted as 0, the provider never answered: A-1 (3 tries), B-1 repeat 2 (4 tries)"


def test_notes_cell_and_failures_notes_show_it():
    from crucibleforge import report
    s = {"forfeits": [{"case_id": "PATCHFIX-core-4-1", "repeat": 1, "attempts": 5}]}
    assert "1 job counted as 0: PATCHFIX-core-4-1, the provider never answered in 5 tries" in report.notes_cell(s)
    assert any(n.startswith("1 job counted as 0") for n in report._model_notes("m", {**s, "speed": {}}))
    assert "counted as 0" not in report.notes_cell({})


# ------------------------------------------------------------------ the guard (declare)

def _csv(folder, details):
    with open(folder / "runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["ts", "model_label", "category", "case_id", "repeat", "grade",
                                           "grade_detail"])
        w.writeheader()
        w.writerow({"ts": "2026-11-20T00:00:00+00:00", "model_label": "m", "category": "coding",
                    "case_id": "OTHER-1", "repeat": "1", "grade": "pass", "grade_detail": "ok"})
        for i, d in enumerate(details):
            w.writerow({"ts": f"2026-11-2{i}T00:00:00+00:00", "model_label": "m", "category": "coding",
                        "case_id": CASE, "repeat": "1", "grade": "error", "grade_detail": d})


@pytest.fixture
def missing_error(monkeypatch):
    monkeypatch.setattr(forfeit45, "missing_reason", lambda *a, **k: "error")


def test_declare_writes_the_record_with_every_attempt(folder, missing_error):
    _csv(folder, [DETAIL] * 4 + ["transport failure: server error: unavailable"])
    rc, line = forfeit45.declare(folder, "m", CASE, 1, "never answered", None, now="2026-11-27T00:00:00+00:00")
    assert rc == 0 and "forfeit recorded" in line
    body = json.loads((folder / forfeit45.FILENAME).read_text(encoding="utf-8"))
    assert len(body) == 1
    rec = body[0]
    assert (rec["model"], rec["case_id"], rec["repeat"], rec["reason"]) == ("m", CASE, 1, "never answered")
    assert rec["declared"] == "2026-11-27T00:00:00+00:00"
    assert [a["detail"] for a in rec["attempts"]] == [DETAIL] * 4 + ["transport failure: server error: unavailable"]
    assert rec["attempts"][0]["ts"] == "2026-11-20T00:00:00+00:00"
    # already on record: refused; --undo removes it
    assert forfeit45.declare(folder, "m", CASE, 1, "again", None)[0] == 1
    rc, line = forfeit45.declare(folder, "m", CASE, 1, "", None, undo=True)
    assert rc == 0 and json.loads((folder / forfeit45.FILENAME).read_text(encoding="utf-8")) == []
    assert forfeit45.declare(folder, "m", CASE, 1, "", None, undo=True)[0] == 1


def test_declare_needs_three_error_rows(folder, missing_error):
    _csv(folder, [DETAIL] * 2)
    rc, line = forfeit45.declare(folder, "m", CASE, 1, "never answered", None)
    assert rc == 1 and "2 stored error row(s)" in line
    assert not (folder / forfeit45.FILENAME).exists()


def test_declare_refuses_a_policy_block(folder, missing_error):
    _csv(folder, [DETAIL, DETAIL, "HTTP 400: content policy violation"])
    rc, line = forfeit45.declare(folder, "m", CASE, 1, "never answered", None)
    assert rc == 1 and "policy block" in line
    assert not (folder / forfeit45.FILENAME).exists()


def test_declare_needs_a_reason(folder, missing_error):
    _csv(folder, [DETAIL] * 3)
    assert forfeit45.declare(folder, "m", CASE, 1, "  ", None)[0] == 1


@pytest.mark.parametrize("why", [None, "empty", "unjudged"])
def test_declare_refuses_a_job_not_missing_with_reason_error(folder, monkeypatch, why):
    monkeypatch.setattr(forfeit45, "missing_reason", lambda *a, **k: why)
    _csv(folder, [DETAIL] * 3)
    rc, line = forfeit45.declare(folder, "m", CASE, 1, "never answered", None)
    assert rc == 1 and "refused" in line
    assert not (folder / forfeit45.FILENAME).exists()


def test_the_cli_command_is_wired(folder, monkeypatch, capsys, missing_error):
    from crucibleforge import cli
    _csv(folder, [DETAIL] * 3)
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: {})
    with pytest.raises(SystemExit) as e:
        cli.main(["--results", str(folder), "forfeit", "--models", "m", "--case", CASE, "--reason", "never"])
    assert e.value.code == 0
    assert json.loads((folder / forfeit45.FILENAME).read_text(encoding="utf-8"))[0]["case_id"] == CASE
    with pytest.raises(SystemExit) as e:
        cli.main(["--results", str(folder), "forfeit", "--models", "m", "--case", CASE, "--reason", "again"])
    assert e.value.code == 1
    assert "already on record" in capsys.readouterr().err


# ------------------------------------------------------------------ the combined board (release45 c-assemble)

def test_the_combined_board_carries_each_folders_forfeits(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import release45 as R
    import test_release45 as T
    monkeypatch.setattr(R.Release, "preflight_env", lambda self: None)
    a = T.mkfolder(tmp_path, "r1", "model-a")
    T.mkfolder(tmp_path, "r2", "model-b")
    _record(a, model="model-a")
    rc, _ = T.go(tmp_path, monkeypatch, ["--only", "c-assemble"])
    assert rc == 0
    board = tmp_path / "board45"
    body = json.loads((board / forfeit45.FILENAME).read_text(encoding="utf-8"))
    assert [(r["model"], r["case_id"]) for r in body] == [("model-a", CASE)]
    # the board reads it keyed by model: model-a's job is forfeited there, model-b's is not
    monkeypatch.setattr(config, "RESULTS_DIR", board)
    forfeit45._CACHE.clear()
    assert forfeit45.apply("model-a", [_err()], frozenset(), _fail)[0]["grade"] == "fail"
    assert forfeit45.apply("model-b", [_err()], frozenset(), _fail)[0]["grade"] == "error"
    # undone in its folder: the next assemble (the folder's signature changed) drops it from the board
    (a / forfeit45.FILENAME).unlink()
    rc, _ = T.go(tmp_path, monkeypatch, ["--only", "c-assemble"])
    assert rc == 0 and not (board / forfeit45.FILENAME).exists()
