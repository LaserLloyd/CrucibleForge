"""Lead decision 2026-11-19: NSFW+ shows two numbers (NSFW, Uncensored); the summed "NSFW + Uncensored" column is
on no visitor-facing board (Markdown or HTML); the sum may stay in the JSON."""
from __future__ import annotations

from _lane_r_toy import PASS, board, html_data, prof45, toys  # noqa: F401 (toys: the fixture that registers the toy family)


def test_no_summed_column_on_the_boards_but_the_sum_stays_in_json(tmp_path, monkeypatch):
    prof = {**prof45(), "release_seed": "4.6.0"}           # a board that prints its NSFW numbers
    subs = prof["score_subtotals"]
    subs["total"] = {"label": "NSFW + Uncensored", "score": "nsfw", "kind": "total", "of_subtotals": ["nsfw", "uncensored"],
                     "of": 20.0, "parts": subs["nsfw"]["parts"] + subs["uncensored"]["parts"]}
    md, stats, html = board(tmp_path, monkeypatch, {"m": PASS}, prof=prof)
    assert "NSFW + Uncensored" not in md
    assert "NSFW (/10)" in md and "Uncensored (/10)" in md
    r = html_data(html)["rows"][0]
    assert "adult_total" not in (r.get("cell_text") or {}) and "adult_total" not in (r.get("s") or {})
    assert "NSFW + Uncensored" not in (r.get("points_cell") or "")
    assert stats["m"]["scorecard"]["subtotals"]["total"]["kind"] == "total"
