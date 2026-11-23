"""F4 of the first fresh reference run (2026-11-19): Overall follows the "never mix maxima" rule. Toy board."""
from __future__ import annotations

from crucibleforge import report

from _lane_r_toy import PASS, board, toys  # noqa: F401  (toys: autouse fixture)
from test_lane_r_r1_truncation_20261118 import _cut_first


# F4: a counted standard part that is "not measured" leaves Overall as "x of y", not ranked, with the reason.
# (The standing rule it does not touch: a part pending validation adds nothing and never stops the ranking.)
def test_overall_is_x_of_y_and_unranked_when_a_counted_part_is_not_measured(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": _cut_first(2), "ok": PASS})
    card = stats["m"]["scorecard"]
    assert card["total"] is None and "not measured (A)" in card["unranked_why"]
    hl = stats["m"]["headline"]
    full = sum(float(p["weight"]) for d in stats["ok"]["scorecard"]["score_defs"] if d.get("in_total", True)
               for p in d["parts"])
    assert 0 < hl["overall_measured"]["of"] < full          # the measured maximum, never the full one
    cell = report.headline_cell(hl, "overall")
    assert "±" in cell or "(margin not computed)" in cell                # never a bare number
    assert f" of {hl['overall_measured']['of']:g}" in cell and "not ranked: not measured (A)" in cell
    assert stats["ok"]["scorecard"]["total"] is not None             # a clean row stays ranked
    assert report.rank_labels(["m", "ok"], stats) == ["ok", "m"]
