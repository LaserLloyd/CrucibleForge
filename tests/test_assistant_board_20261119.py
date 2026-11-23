"""Assistant basics (checked), step 4: the column on the standard page (md, html, json), its footnote and its words.
It sits outside Overall: no rank, no sort and no other cell changes with it. The 4.1 page is untouched (guarded by
the 4.1 golden tests)."""
from __future__ import annotations

import json

import pytest

from crucibleforge import assistant45, report

from _lane_r_toy import PASS, board, cell, head, html_data, prof45, toys  # noqa: F401  (toys: autouse fixture)

LABEL = "Assistant basics (checked)"


def _prof(validated=True):
    p = prof45()
    p["assistant"] = {"label": LABEL, "short": "Basics", "cases": ["IZ02-json-single-line-typed"], "repeats": 2,
                      "bands": {"pass": 0.85, "partial": 0.40}, "z": 1.645, "validated": validated}
    return p


def _a(share, **kw):
    base = {"status": "ok", "share": share, "margin": 0.06, "band": assistant45.band(share, {"pass": .85, "partial": .4}),
            "n_items": 15, "n_runs": 30, "expected_runs": 30, "cut": 0, "missing": 0, "validated": True}
    return {**base, **kw}


@pytest.fixture
def canned(monkeypatch):
    """Give each model its canned block as stats_from_tier_rows builds it (the toy rows hold no real item rows)."""
    table = {}
    real = report.stats_from_tier_rows

    def wrapped(label, *a, **k):
        st = real(label, *a, **k)
        if label in table:
            st["assistant45"] = table[label]
        return st
    monkeypatch.setattr(report, "stats_from_tier_rows", wrapped)
    return table


def test_the_column_sits_beside_the_pack_points_with_its_band_and_a_footnote(tmp_path, monkeypatch, canned):
    canned.update(m1=_a(0.97), m2=_a(0.96))
    md, stats, html = board(tmp_path, monkeypatch, {"m1": PASS, "m2": PASS}, prof=_prof())
    h = head(md)
    assert h.index(LABEL) == h.index("Pack points") + 1
    assert cell(md, "m1", LABEL) == "pass 97% ± 6 ‡"
    assert cell(md, "m2", LABEL) == "pass 96% ± 6 ‡"
    assert assistant45.NOTE in md and "does not rank good models" in assistant45.NOTE


def test_the_cell_says_pending_until_validated_and_not_run_without_rows(tmp_path, monkeypatch, canned):
    canned.update(m1=_a(0.97))
    md, *_ = board(tmp_path, monkeypatch, {"m1": PASS, "m2": PASS}, prof=_prof(validated="pending-machine"))
    assert cell(md, "m1", LABEL) == "pending validation"
    assert cell(md, "m2", LABEL) == "not run"


def test_no_mark_when_a_model_below_the_line_shares_the_board(tmp_path, monkeypatch, canned):
    canned.update(m1=_a(0.97), m2=_a(0.30))
    md, *_ = board(tmp_path, monkeypatch, {"m1": PASS, "m2": PASS}, prof=_prof())
    assert cell(md, "m1", LABEL) == "pass 97% ± 6" and cell(md, "m2", LABEL) == "weak 30% ± 6"


def test_a_board_without_the_block_has_no_column(tmp_path, monkeypatch):
    md, _stats, html = board(tmp_path, monkeypatch, {"m1": PASS})
    assert LABEL not in md and assistant45.NOTE not in md
    assert all(c["key"] != "assistant" for c in html_data(html)["main"])


def test_the_html_row_and_json_carry_the_block_and_a_text_cell(tmp_path, monkeypatch, canned):
    canned.update(m1=_a(0.97), m2=_a(0.96))
    _md, stats, html = board(tmp_path, monkeypatch, {"m1": PASS, "m2": PASS}, prof=_prof())
    data = html_data(html)
    rows = {r["label"]: r for r in data["rows"]}
    assert rows["m1"]["cell_text"]["assistant"] == "pass 97% ± 6 ‡"
    assert rows["m1"]["assistant45"]["share"] == 0.97 and rows["m2"]["assistant45"]["band"] == "pass"
    assert any(c["key"] == "assistant" and c["label"] == LABEL for c in data["main"]), data.keys()
    assert assistant45.NOTE in html


def test_overall_rank_order_and_every_other_cell_are_unchanged_by_the_column(tmp_path, monkeypatch, canned):
    canned.update(m1=_a(0.10), m2=_a(1.0))
    with_md, with_stats, _ = board(tmp_path / "a", monkeypatch, {"m1": PASS, "m2": PASS}, prof=_prof())
    canned.clear()
    without_md, without_stats, _ = board(tmp_path / "b", monkeypatch, {"m1": PASS, "m2": PASS})
    cols = [c for c in head(with_md) if c not in ("", LABEL)]
    for m in ("m1", "m2"):
        for c in cols:
            if c in ("Run date",):
                continue
            assert cell(with_md, m, c) == cell(without_md, m, c), (m, c)
    assert [cell(with_md, m, "#") for m in ("m1", "m2")] == [cell(without_md, m, "#") for m in ("m1", "m2")]
    assert {l: s["headline"]["rank"] for l, s in with_stats.items()} == \
           {l: s["headline"]["rank"] for l, s in without_stats.items()}
