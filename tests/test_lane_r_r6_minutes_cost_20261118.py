"""R6 (fix round 2026-11-18, REVIEW finding 11): minutes come from ``meta.timing`` (the phases of the run command,
summed) and dollars from the rows' recorded cost, printed per row. The check: the printed minutes are within 2% of
the run's wall clock on a synthetic meta. A row without timing prints "not recorded". Toy board: no model, no rig."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from crucibleforge import report

from _lane_r_toy import PASS, board, cell, head, html_data, toys  # noqa: F401

T0 = datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc)
PHASES = [{"phase": "generation", "s": 2950.4}, {"phase": "judge", "s": 610.2}, {"phase": "report", "s": 33.8}]
WALL_S = 2950.4 + 610.2 + 33.8 + 20        # 20 s of start-up the phases do not cover


def _meta():
    return {"timing": PHASES, "bench_run_id": "r1", "started": T0.isoformat(),
            "judged": (T0 + timedelta(seconds=WALL_S)).isoformat()}


def _one_run(m, r):
    r["bench_run_id"] = "r1"            # the toy rows of one run: every row carries the meta's run id
    return True


def test_minutes_unit():
    assert report.timing_minutes45({"timing": PHASES}) == round(sum(p["s"] for p in PHASES) / 60, 1)
    assert report.timing_minutes45({}) is None
    assert report.timing_minutes45({"timing": [{"phase": "x"}]}) is None


def test_printed_minutes_are_within_two_percent_of_the_wall_clock(tmp_path, monkeypatch):
    md, stats, html = board(tmp_path, monkeypatch, {"m": PASS, "o": PASS}, metas={"m": _meta(), "o": {"timing": []}},
                            keep=_one_run)
    assert "Minutes" in head(md)
    printed = float(cell(md, "m", "Minutes"))
    wall = WALL_S / 60
    assert abs(printed - wall) / wall <= 0.02, (printed, wall)
    assert cell(md, "o", "Minutes") == "not recorded"
    rows = {r["label"]: r for r in html_data(html)["rows"]}
    assert rows["m"]["minutes"] == printed and rows["o"]["minutes"] is None
    assert stats["m"]["run_minutes45"] == printed


def test_dollars_from_the_rows_cost_per_row(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": PASS}, metas={"m": _meta()})
    assert "Run cost" in head(md)
    # the toy rows carry no recorded cost and no price: an unpriced local row prints the rig dash
    assert cell(md, "m", "Run cost") in ("—", "-")


def test_rows_from_several_runs_are_not_timed():
    meta = {"timing": PHASES, "bench_run_id": "r2"}
    assert report.timing_minutes45(meta, [{"bench_run_id": "r2"}, {"bench_run_id": "r1"}]) is None
    assert report.timing_minutes45(meta, [{"bench_run_id": "r2"}]) is not None
    assert report.timing_minutes45(meta, [{}]) is None
    assert report.minutes45_cell({"run_minutes45": None, "run_ids45": 3}) == "not recorded (rows from 3 runs)"
