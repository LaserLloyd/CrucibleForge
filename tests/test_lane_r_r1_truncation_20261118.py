"""R1 (fix round 2026-11-18, D10 as amended): ONE truncation rule for every part. A reply cut at its token ceiling
is left out of the part's mean and counted; the part prints "not measured (n of m replies cut)" only when more than
a quarter of its units are cut AND at least two units are cut (on a three-unit part one long think must not void
it). The row's notes list the cut and empty counts per part. Toy board (G2's toy families): no model, no rig."""
from __future__ import annotations

import pytest

from crucibleforge import profiles, report, tiers

from _lane_r_toy import PASS, board, cut, prof45, row, toys  # noqa: F401  (toys: autouse fixture)


@pytest.mark.parametrize("n_cut,n,want", [
    (0, 3, False), (1, 3, False), (2, 3, True),      # three units: one cut never voids the part
    (1, 2, False), (2, 2, True),
    (2, 8, False), (3, 8, True),                      # a quarter exactly is still measured
    (1, 1, False), (5, 20, False), (6, 20, True)])
def test_the_one_rule(n_cut, n, want):
    assert report.cut_not_measured(n_cut, n) is want


def _rows(n, n_cut, empty=0):
    out = []
    for i in range(n):
        r = {"case_id": f"C{i}", "repeat": 1, "turn": None, "response": "x", "finish_reason": "stop"}
        if i < n_cut:
            r.update(finish_reason="length", truncated=True, response="half an answ")
        elif i < n_cut + empty:
            r.update(response="")
        out.append(r)
    return out


def test_case_part_one_cut_of_three_is_left_out_and_counted():
    rows, rec = report.case_part_rows(_rows(3, 1), "exclude")
    assert len(rows) == 2 and rec["ran_out"] == 1 and rec["calls"] == 3
    assert rec["not_measured"] is False


def test_case_part_two_cut_of_three_is_not_measured():
    rows, rec = report.case_part_rows(_rows(3, 2), "exclude")
    assert rows == [] and rec["not_measured"] is True and rec["ran_out"] == 2


def test_case_part_counts_empty_replies():
    _rows_, rec = report.case_part_rows(_rows(5, 1, empty=2), "exclude")
    assert rec["empty"] == 2 and rec["ran_out"] == 1


def test_every_part_of_the_shipped_standard_page_uses_the_exclude_rule(monkeypatch):
    """One rule for every part: every in-total part of bench45 (case parts and every chain set) excludes cut
    replies; none scores a cut reply as the model's 0."""
    from crucibleforge import chains, scale
    monkeypatch.setenv("CF_COMPOSITION", "standard")
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    rules = scale.ran_out_rules(p)
    for d in tiers.scores(p):
        if not d.get("in_total", True):
            continue
        for part in d.get("parts") or []:
            key = f"{d['key']}/{part['key']}"
            if part.get("cases"):
                assert report.case_part_ran_out(p, part) == "exclude", key
            cells = {scale.cell_of({"family": r.get("family"), "level": r.get("level"), "kind": c["chain_spec"].get("kind"),
                                    "items": c["chain_spec"].get("items")})
                     for c in chains.chain_cases(p) if c["chain_spec"].get("part") == key
                     for r in c["chain_spec"].get("rungs") or []}
            for cell in cells:
                assert rules.get(cell) == "exclude", (key, cell)


def _cut_first(n_cut, part="xa", empty=0):
    """Policy: the first ``n_cut`` units of ``part`` are cut at the ceiling, the next ``empty`` answer nothing."""
    def pol(lv, cid):
        if cid.startswith(f"FLAT-{part}-"):
            k = int(cid.split("-")[2])
            if k <= n_cut:
                return cut("PAS")
            if k <= n_cut + empty:
                return ""
        return "PASS"
    return pol


def test_one_cut_unit_of_four_leaves_the_mean_and_is_counted(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": _cut_first(1)})
    card = stats["m"]["scorecard"]
    assert card["parts"]["work"]["a"] == pytest.approx(5.0)       # the three answered units, all right
    det = card["ladder"]["work"]["a"]
    assert det["cut"] == {"cut": 1, "of": 4, "empty": 0} and not det.get("not_measured")
    notes = row(md, "m")[-2]
    assert "cut at the ceiling (left out of the score): A 1 of 4" in notes, notes


def test_two_cut_units_of_four_print_not_measured_with_the_count(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": _cut_first(2)})
    card = stats["m"]["scorecard"]
    assert card["parts"]["work"]["a"] is None
    pts = next(x for x in md.splitlines() if x.startswith("| m |") and " = A " in x)
    assert "A not measured (2 of 4 replies cut)" in pts, pts
    notes = row(md, "m")[-2]
    assert "A 2 of 4" in notes


def test_empty_replies_are_counted_per_part(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": _cut_first(0, empty=1)})
    assert stats["m"]["scorecard"]["ladder"]["work"]["a"]["cut"]["empty"] == 1
    notes = row(md, "m")[-2]
    assert "empty replies (scored 0): A 1" in notes, notes


def test_a_clean_row_has_no_cut_note(tmp_path, monkeypatch):
    md, _stats, _ = board(tmp_path, monkeypatch, {"m": PASS})
    assert "cut at the ceiling" not in row(md, "m")[-2]
