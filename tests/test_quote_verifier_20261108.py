"""Quote verifier (JUDGE-AUDIT-41, 2026-11-08): a " / " splice verifies per
piece, markdown emphasis is ignored on both sides, and nothing else got
looser."""
from __future__ import annotations

import json

from crucibleforge import judge

TEXT = ("The ledger was opened on **23 February** by the clerk.\n"
        "Later the *quiet* gate closed behind them and nobody spoke.")


def test_a_slash_splice_verifies_when_every_piece_is_in_the_reply():
    q = "The ledger was opened on 23 February / nobody spoke."
    # second piece is under the 12-char floor? no: 'nobody spoke.' is 13
    assert judge.quote_found(q, TEXT)
    q2 = "opened on 23 February by the clerk / the quiet gate closed behind them"
    assert judge.quote_found(q2, TEXT)


def test_a_slash_splice_fails_when_one_piece_is_missing():
    assert not judge.quote_found(
        "opened on 23 February by the clerk / the loud gate slammed shut", TEXT)


def test_a_short_piece_cannot_verify_a_splice():
    assert not judge.quote_found("opened on 23 February by the clerk / gate", TEXT)
    assert not judge.quote_found("the clerk / by the clerk", "by the clerk")


def test_a_literal_slash_in_the_reply_still_verifies_whole():
    assert judge.quote_found("rise up high / fall down low", "rise up high / fall down low")


def test_dropped_emphasis_markers_verify():
    assert judge.quote_found("The ledger was opened on 23 February by the clerk", TEXT)
    assert judge.quote_found("the quiet gate closed behind them", TEXT)
    assert judge.quote_found("opened on **23 February** by", TEXT)   # kept is fine too
    assert judge.quote_found("opened on __23 February__ by", TEXT)


def test_still_strict_otherwise():
    assert not judge.quote_found("The ledger was opened on 24 February by the clerk", TEXT)
    assert not judge.quote_found("the quiet gate shut behind them", TEXT)
    assert not judge.quote_found("opened on 23 Febr by the", TEXT.replace("uary", "xx"))


def test_reverify_reports_flips_and_writes_nothing(tmp_path):
    row = {"case_id": "Q", "model_label": "m", "response": TEXT,
           "judge": {"elements": {"results": [
               {"id": "a", "pass": False, "judge_pass": True,
                "quote": "The ledger was opened on 23 February by the clerk",
                "detail": "judge said yes but only 0/1 samples quoted the reply verbatim (quote not in the reply)"},
               {"id": "b", "pass": False, "judge_pass": True,
                "quote": "never said anywhere at all",
                "detail": "judge said yes but only 0/1 samples quoted the reply verbatim (quote not in the reply)"},
               {"id": "c", "pass": False, "judge_pass": False, "quote": "", "detail": "judge: no"}]}}}
    f = tmp_path / "transcripts_m.jsonl"
    f.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before = f.read_bytes()
    rep = judge.reverify_quotes(tmp_path)
    assert f.read_bytes() == before
    assert rep["rows_scanned"] == 1
    assert [(x["case_id"], x["element"], x["flips"]) for x in rep["flips"]] == [("Q", "a", True)]
    assert rep["flip_count"] == 1


def test_reverify_cli_is_a_dry_run(tmp_path, capsys, monkeypatch):
    from crucibleforge import cli, config
    row = {"case_id": "Q", "model_label": "m", "response": TEXT,
           "judge": {"elements": {"results": [
               {"id": "a", "pass": False, "judge_pass": True,
                "quote": "the quiet gate closed behind them",
                "detail": "judge said yes but only 0/1 samples quoted the reply verbatim"}]}}}
    f = tmp_path / "transcripts_m.jsonl"
    f.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before = f.read_bytes()
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    assert cli.cmd_reverify_quotes(type("A", (), {"json": False})(), {}) == 0
    out = capsys.readouterr().out
    assert "would flip to PASS: 1" in out
    assert f.read_bytes() == before
