"""2026-09-26 sanity audit: rows of a case that is no longer in the benchmark
profile must not reach the board (they inflated a pass rate and coverage)."""
import json

from crucibleforge import config, report


def _row(cid, cat, grade, rev=None):
    from crucibleforge.version import revision
    return {"bench_run_id": "r1", "bench_revision": rev or revision(), "profile": "bench",
            "case_id": cid, "repeat": 1, "turn": None, "category": cat, "grade": grade,
            "ts": "2026-09-25T01:00:00+00:00", "metrics": {"tok_per_s": 30.0}}


def test_retired_case_ids_stay_off_the_board(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    rows = [_row("RX02-knights-knaves", "reasoning", "fail"),
            # a case id that left the profile (the saturation run's old RX20)
            _row("RX20-five-houses-six-attributes", "reasoning", "pass"),
            _row("RX21-eleven-islanders-two-days", "reasoning", "pass")]
    (tmp_path / "transcripts_m.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / "meta_m.json").write_text(json.dumps({"model_id": "x/y", "profile": "bench"}))
    kept = report.board_filter("m", None)
    assert [r["case_id"] for r in kept] == ["RX02-knights-knaves"]
    _, stats, _ = report.board_stats(None, None)
    assert stats["m"]["reasoning"]["n"] == 1 and stats["m"]["reasoning"]["passed"] == 0
    assert stats["m"]["coverage"]["cases"] == 1
