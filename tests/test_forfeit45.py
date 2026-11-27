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
    assert forfeit45.applied("m", kept) == [{"case_id": CASE, "repeat": 1, "kind": "no_answer", "reason": "never answered",
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


# ------------------------------------------------------------------ ungradable jobs (owner 2026-11-27: standardised)

def _csv_rows(folder, rows):
    with open(folder / "runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["ts", "model_label", "category", "case_id", "repeat", "grade",
                                           "finish_reason", "grade_detail"])
        w.writeheader()
        for i, (fin, grade) in enumerate(rows):
            w.writerow({"ts": f"2026-11-2{i}T00:00:00+00:00", "model_label": "m", "category": "rp",
                        "case_id": CASE, "repeat": "2", "grade": grade, "finish_reason": fin, "grade_detail": ""})


@pytest.mark.parametrize("why", forfeit45.JUDGE_GAPS)
def test_an_ungradable_job_is_recorded_with_the_flag(folder, monkeypatch, why):
    monkeypatch.setattr(forfeit45, "missing_reason", lambda *a, **k: why)
    _csv_rows(folder, [("stop", ""), ("stop", ""), ("length", "")])
    rc, line = forfeit45.declare(folder, "m", CASE, 2, "judge could not grade it", None, after_rejudge=True,
                                 now="2026-11-27T00:00:00+00:00")
    assert rc == 0 and "forfeit recorded" in line
    rec = json.loads((folder / forfeit45.FILENAME).read_text(encoding="utf-8"))[0]
    assert rec["kind"] == forfeit45.UNGRADABLE and rec["missing_reason"] == why and rec["repeat"] == 2
    assert rec["stored_rows"] == 3 and rec["finish_reasons"] == {"length": 1, "stop": 2}
    assert rec["attempts"] == []
    assert set(rec) == {"model", "case_id", "repeat", "kind", "reason", "declared", "missing_reason",
                        "stored_rows", "finish_reasons", "attempts"}     # status fields only, never a row's text


@pytest.mark.parametrize("why", forfeit45.JUDGE_GAPS)
def test_an_ungradable_job_is_refused_without_the_flag(folder, monkeypatch, why):
    monkeypatch.setattr(forfeit45, "missing_reason", lambda *a, **k: why)
    _csv_rows(folder, [("stop", "")])
    rc, line = forfeit45.declare(folder, "m", CASE, 2, "judge could not grade it", None)
    assert rc == 1 and "--after-rejudge" in line
    assert not (folder / forfeit45.FILENAME).exists()


@pytest.mark.parametrize("why", [None, "empty", "ctx_short", "content_filter"])
def test_the_flag_still_needs_a_judge_gap_now(folder, monkeypatch, why):
    monkeypatch.setattr(forfeit45, "missing_reason", lambda *a, **k: why)
    _csv_rows(folder, [("stop", "")])
    rc, line = forfeit45.declare(folder, "m", CASE, 2, "judge could not grade it", None, after_rejudge=True)
    assert rc == 1 and "refused" in line
    assert not (folder / forfeit45.FILENAME).exists()


def test_the_flag_does_not_loosen_the_no_answer_guard(folder, missing_error):
    _csv(folder, [DETAIL] * 2)
    rc, line = forfeit45.declare(folder, "m", CASE, 1, "never answered", None, after_rejudge=True)
    assert rc == 1 and "2 stored error row(s)" in line
    _csv(folder, [DETAIL] * 3)
    assert forfeit45.declare(folder, "m", CASE, 1, "never answered", None, after_rejudge=True)[0] == 0
    assert json.loads((folder / forfeit45.FILENAME).read_text(encoding="utf-8"))[0]["kind"] == forfeit45.NO_ANSWER


def _judged(**kw):
    r = {"case_id": CASE, "repeat": 2, "turn": None, "category": "rp", "grade": None, "needs_judge": True,
         "rubric": "rp_deep", "response": "a reply", "finish_reason": "stop", "model_label": "m"}
    r.update(kw)
    return r


def _record_kind(folder, kind, repeat=2):
    (folder / forfeit45.FILENAME).write_text(json.dumps([{
        "model": "m", "case_id": CASE, "repeat": repeat, "kind": kind, "reason": "x",
        "declared": "2026-11-27T00:00:00+00:00", "attempts": []}]), encoding="utf-8")


def test_each_kind_applies_only_to_its_own_reasons(folder):
    unjudged = _judged()                                                   # needs the judge, has no verdict
    failed = _judged(judge={"judge_failed": True})
    assert scoring45.job_missing_reason([unjudged]) == "unjudged"
    assert scoring45.job_missing_reason([failed]) == "judge_failed"
    _record_kind(folder, forfeit45.UNGRADABLE)
    for r in (unjudged, failed):
        out = forfeit45.apply("m", [r], frozenset(), _fail)
        assert out[0]["forfeit"] is True
        assert forfeit45.split_missing(_by_t(out))[1] == []
    assert forfeit45.apply("m", [_err(repeat=2)], frozenset(), _fail)[0]["grade"] == "error"   # not a judge gap
    _record_kind(folder, forfeit45.NO_ANSWER)
    rows = [unjudged]
    assert forfeit45.apply("m", rows, frozenset(), _fail) is rows                             # not an error job
    kept = forfeit45.split_missing(_by_t(forfeit45.apply("m", [_err(repeat=2)], frozenset(), _fail)))[0]
    assert forfeit45.applied("m", kept)[0]["kind"] == forfeit45.NO_ANSWER


def test_a_record_without_a_kind_is_a_no_answer_forfeit(folder):
    _record(folder)
    assert forfeit45.records_for("m")[(CASE, 1)].get("kind") is None
    assert forfeit45.apply("m", [_err()], frozenset(), _fail)[0]["forfeit"] is True
    rows = [_judged(repeat=1)]
    assert forfeit45.apply("m", rows, frozenset(), _fail) is rows


def test_the_ungradable_note():
    f = {"case_id": "S-1", "repeat": 1, "kind": forfeit45.UNGRADABLE}
    assert forfeit45.note([f]) == "1 job counted as 0: S-1, the judge could not grade the answer after a second pass"
    two = forfeit45.note([f, {"case_id": "P-1", "repeat": 1, "attempts": 5}])
    assert two == ("2 jobs counted as 0: S-1, the judge could not grade the answer after a second pass; "
                   "P-1, the provider never answered in 5 tries")


def test_a_judged_scene_scores_zero_like_a_graded_failure():
    """The forfeited row of a judged scene is a long-scene unit whose every judged element failed and whose
    empty-generation verdict zeroes every item: the scene's score is 0, exactly the counted-failed rule."""
    from crucibleforge import agree, rpdeep
    from crucibleforge.report import _forfeit_failed
    case = {"id": CASE, "turns": [{"user": "t1"}], "judge_elements": [{"id": "F1", "fact": "f1"},
                                                                       {"id": "F2", "fact": "f2", "weight": 2}]}
    row = _judged(conversation=[{"role": "assistant", "content": "a reply"}])
    r = _forfeit_failed(row, case)
    j = r["judge"]
    assert j["empty_generation"] and j["counted_failed"] == "forfeit"
    assert [(e["id"], e["pass"]) for e in j["elements"]["results"]] == [("F1", False), ("F2", False)]
    assert scoring45.job_missing_reason([r]) is None                        # a verdict, not a judge gap
    sr = rpdeep.score_row(r, case)
    assert sr["status"] == "ok" and sr["rate"] == 0.0
    # the graded failure: the judge's own verdict with every element NO
    graded = {**row, "judge": {"judge_failed": False, "refused": False, "scores": {},
                               "elements": {"results": [{"id": "F1", "pass": False}, {"id": "F2", "pass": False}]}}}
    assert rpdeep.score_row(graded, case)["rate"] == sr["rate"] == 0.0
    monkey_units = {**r, "case_id": "RD4-kettle-saddle"}                       # a long-scene id: ids only
    assert list(agree.long_units([monkey_units]))                            # the scorer picks the unit up
