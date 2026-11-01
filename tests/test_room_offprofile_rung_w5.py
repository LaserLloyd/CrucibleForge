"""W5 (2026-11-01): `escalate --rungs 131072` on a profile whose top rung is
65536 wrote good rows that the Room view silently dropped (review L2 cap), so
the report kept saying "ran out, retry pending". A rung the label's meta
records as run (not failed) now counts."""
import json

from crucibleforge import config, report, runner

from test_escalation_20261025 import _row

CID = "UX1-tide-cell"


def _seed(tmp_path, monkeypatch, rows, escalated):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / "transcripts_m.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (tmp_path / "meta_m.json").write_text(
        json.dumps({"started": "2026-10-25T01:00:00+00:00", "escalated": escalated}),
        encoding="utf-8")


def _rows():
    base = _row(CID, cat="nsfw")
    esc = _row(CID, cat="nsfw", finish="stop", rung=131072, sent=131072, run="e1",
               ts="2026-10-25T02:00:00+00:00")
    return [base, esc]


def test_an_off_profile_rung_that_ran_counts_in_the_room_view(tmp_path, monkeypatch):
    ok = {"ts": "2026-10-25T02:00:00+00:00", "rung": 131072, "failed": False, "rows": 1}
    _seed(tmp_path, monkeypatch, _rows(), [ok])
    assert runner.escalated_rungs("m") == {131072}
    room = report.board_filter("m", None, with_rungs=True)
    assert [r["bench_run_id"] for r in room] == ["e1"]


def test_without_a_run_record_the_cap_still_drops_old_wide_rows(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _rows(), [])
    room = report.board_filter("m", None, with_rungs=True)
    assert [r["bench_run_id"] for r in room] == ["r1"]


def test_the_empty_stop_retry_is_recorded_on_the_row():
    from crucibleforge.api import ChatResult
    r = ChatResult(response_text="a", finish_reason="stop")
    r.empty_retry = True
    row = {}
    runner._annotate_recovery(row, r)
    assert row == {"empty_retry": True}
    other = {}
    runner._annotate_recovery(other, ChatResult(response_text="a", finish_reason="stop"))
    assert other == {}
