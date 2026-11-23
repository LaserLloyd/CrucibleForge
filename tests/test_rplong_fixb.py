"""Fix pass B: one pinned reader per graded family for the chat scenes (M7), declared family vs lineage (S2)."""
import pytest

from crucibleforge import agree as AG
from crucibleforge import rplong_mean as M

CASE = "CC3-tea-hut"
UNIT = "r|CC3-tea-hut|1"


def _row(sha="abc"):
    return {"case_id": CASE, "rubric": "rp_deep", "bench_run_id": "r", "repeat": 1,
            "judge": {"instrument": {"rubric_sha": sha}, "elements": {"results": [{"id": "a", "pass": True, "weight": 1}]}},
            "conversation": [{"content": "x"}]}


def _rec(row, model, fam, psha, score=0.5):
    return {"part": AG.LONG_PART, "model": "m", "unit": UNIT, "row_sha": AG.row_sha(row), "reader": model,
            "score_reader": score, "reader_stamp": {"family": fam, "model": model, "prompt_sha": psha}}


READERS = M.load_readers({"local": [{"role": "R-M", "id": "mistral-x", "family": "mistral"}],
                          "hosted": [{"id": "deepseek-v4-flash", "family": "deepseek"}]})


def _score(row, recs, **kw):
    return M.scene_scores([row], recs, label="m", cases={}, graded_family="qwen", readers=READERS, **kw)[UNIT]


def test_record_counts_only_for_the_pinned_reader_and_current_rubric():
    row = _row("abc")
    assert _score(row, [_rec(row, "mistral-x", "mistral", "abc")])["status"] == "ok"
    # a different reader's record never counts, however new
    u = _score(row, [_rec(row, "deepseek-v4-flash", "deepseek", "abc")])
    assert u["status"] == "pending" and "pinned reader" in u["reason"]
    # a record read under an older rubric never counts against the row's current rubric sha
    u = _score(row, [_rec(row, "mistral-x", "mistral", "old")])
    assert u["status"] == "pending"


def test_newest_record_of_another_reader_does_not_replace_the_first():
    row = _row()
    first = _rec(row, "mistral-x", "mistral", "abc", score=1.0)
    later = _rec(row, "deepseek-v4-flash", "deepseek", "abc", score=0.0)
    u = _score(row, [first, later])
    assert u["status"] == "ok" and u["reader"] == 1.0


def test_same_model_id_but_other_family_stamp_is_refused():
    row = _row()
    assert _score(row, [_rec(row, "mistral-x", "openai", "abc")])["status"] == "pending"


def test_pin_from_config_overrides_file_order():
    cfg = M.load_readers({"pins": {"qwen": "deepseek-v4-flash"},
                          "local": [{"role": "R-M", "id": "mistral-x", "family": "mistral"}],
                          "hosted": [{"id": "deepseek-v4-flash", "family": "deepseek"}]})
    assert M.pinned_reader("qwen", cfg).id == "deepseek-v4-flash"
    assert M.pinned_reader("xai", cfg).id == "mistral-x"


def test_no_reader_named_is_pending_with_the_reason():
    cfg = M.load_readers({"local": [{"role": "R-Q", "id": None, "family": "qwen"}]})
    row = _row()
    u = M.scene_scores([row], [], label="m", cases={}, graded_family="qwen", readers=cfg)[UNIT]
    assert u["status"] == "pending" and "no reader" in u["reason"]


def test_declared_family_must_match_lineage():
    r = M.ReaderSpec("Qwen3.5-122B-heretic", "mistral")
    assert M.check_fit(r, "xai") and "lineage" in M.check_fit(r, "xai")
    assert M.check_fit(M.ReaderSpec("Qwen3.5-122B-heretic", "qwen"), "xai") is None
    with pytest.raises(M.NoFittingReader):
        M.pinned_reader("deepseek", M.load_readers({"local": [{"id": "Qwen3.5-122B-heretic", "family": "mistral"}]}),
                        allow_hosted=False)


def test_s10_chat_readers_file_is_a_row_of_the_marks_index():
    import hashlib
    from pathlib import Path
    root = Path(M.__file__).resolve().parent.parent / "calibration"
    rows = [ln for ln in (root / "MARKS-INDEX-45.md").read_text(encoding="utf-8").splitlines()
            if ln.startswith("| `calibration/chat-readers-45.yaml`")]
    assert len(rows) == 1
    sha = rows[0].split("|")[2].strip().strip("`")
    assert sha == hashlib.sha256((root / "chat-readers-45.yaml").read_bytes()).hexdigest()
    assert "before any launch row was read" in rows[0]
    assert not (root / "MARKS-INDEX-45-pending-rows.txt").exists()


def test_imported_agree_records_carry_the_rows_rubric_sha(tmp_path, monkeypatch):
    row = _row("abc")
    rec = {"part": AG.LONG_PART, "model": "m", "unit": UNIT, "row_sha": AG.row_sha(row), "reader": "mistral-x+think",
           "score_reader": 0.5}
    monkeypatch.setattr(AG, "load_records", lambda folder, key: [rec])
    rd = M.ReaderSpec("mistral-x", "mistral")
    out = M.import_agree_records(tmp_path, rd, rows=[row])
    assert out[0]["reader_stamp"]["prompt_sha"] == "abc"
    u = M.scene_scores([row], out, label="m", cases={}, graded_family="qwen", readers=READERS)[UNIT]
    assert u["status"] == "ok"
