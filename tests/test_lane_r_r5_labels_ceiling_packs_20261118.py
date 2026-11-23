"""R5 (fix round 2026-11-18, REVIEW findings 8, 9, 10, D7): the 4.5.0 board names what its items measure, marks the
parts at the ceiling, and shows standard and pack points as two numbers.

- Labels (display only; every key -- score, part, html component key, json -- stays as it was): "Chat" is
  "Role-play and memory", "Coding" is "Coding (small tasks)", the four Coding parts are "Careful small functions",
  "Follows tool rules", "Steps through code", "Knows language traps". The profile's own label stays in
  ``label_profile``.
- Ceiling: a part carries "‡" (a footnoted symbol, not a colour) when at least two models on the board are measured
  on it and every one of them scored at least 95% of its standard questions.
- Two columns: the section cell holds the standard points only; a "Pack points" column holds the pack points."""
from __future__ import annotations

from crucibleforge import profiles, report, tiers

from _lane_r_toy import PASS, board, cell, cut, head, html_data, row, toys  # noqa: F401

NEW = {"work": "Coding (small tasks)", "chat": "Role-play and memory",
       "work/c1_implied": "Careful small functions", "work/c5_tools": "Follows tool rules",
       "work/c2_trace": "Steps through code", "work/c2_predict": "Knows language traps"}


def _bench45(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "standard")
    profiles.set_active("bench45")
    return profiles.load_profile("bench45")


def test_the_shipped_page_names_what_it_measures(monkeypatch):
    p = _bench45(monkeypatch)
    defs = {d["key"]: d for d in tiers.scores(p)}
    for k, lab in NEW.items():
        if "/" in k:
            s, pk = k.split("/")
            part = next(x for x in defs[s]["parts"] if x["key"] == pk)
            assert part["label"] == lab and part["label_profile"] != lab, (k, part)
            assert len(lab.split()) <= 4
        else:
            assert defs[k]["label"] == lab and defs[k]["label_profile"] in ("Coding", "Chat")
    # keys unchanged
    # (+ step 2's two max-0 parts: the seeded tool tasks and the Fixes pack)
    assert [x["key"] for x in defs["work"]["parts"]] == ["c1_implied", "c5_tools", "c2_trace", "c2_predict",
                                                         "c5_toolgen", "c3_fix"]


def test_public_labels_keep_the_new_names(monkeypatch):
    p = {**_bench45(monkeypatch), "_public": True}
    defs = {d["key"]: d for d in tiers.scores(p)}
    assert [x["label"] for x in defs["work"]["parts"]][:4] == [NEW[f"work/{k}"] for k in
                                                               ("c1_implied", "c5_tools", "c2_trace", "c2_predict")]


def test_a_4_1_profile_keeps_its_labels():
    p = {"scores": {"work": {"label": "Coding", "parts": {"c1_implied": {"label": "Builds what was meant (C1)"}}}}}
    d = tiers.scores(p)[0]
    assert d["label"] == "Coding" and d["parts"][0]["label"] == "Builds what was meant (C1)"
    assert "label_profile" not in d


def _fail_a(lv, cid):
    return "FAIL" if cid.startswith("FLAT-xa-00") and cid.endswith(("1", "2")) else "PASS"


def test_the_ceiling_mark_needs_every_model_at_95(tmp_path, monkeypatch):
    md, stats, html = board(tmp_path, monkeypatch, {"m1": PASS, "m2": _fail_a})
    marks = report.ceiling_parts45(list(stats), stats)
    assert "work/b" in marks and "work/a" not in marks
    parts = next(i for i, x in enumerate(md.splitlines()) if x == "## Parts")
    header = md.splitlines()[parts + 2]
    assert "Part b ‡" in header and "Part a ‡" not in header, header
    assert report.CEILING_NOTE in md
    comps = {c["key"]: c for c in html_data(html)["components"]}
    assert comps["work/b"]["ceiling"] is True and not comps["work/a"].get("ceiling")


def test_one_model_alone_never_marks_a_ceiling(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m1": PASS})
    assert report.ceiling_parts45(list(stats), stats) == set()
    assert "‡" not in md


def test_standard_and_pack_points_are_two_numbers():
    d = {"key": "work", "parts": [{"key": "a", "weight": 5}, {"key": "b", "weight": 5}]}
    hl = {"S": {"work": 10.7}, "T1": {"work": 9.4}, "m": {"work": 1.0},
          "P": {"work/a": 5.4, "work/b": 5.3}, "unscaled": {"work": {"of": 12.0, "pending": []}}}
    assert report.section_cell45(hl, d) == "**9**.4"
    assert report.packs_cell45(hl, [d]) == "+1.3 of 2 (total 10.7 ± 1.0)"
    hl2 = {"S": {"work": 9.4}, "T1": {"work": 9.4}, "m": {"work": 0.5}, "P": {"work/a": 5.0, "work/b": 4.4},
           "unscaled": {"work": {"of": 10.0, "pending": []}}}
    assert report.section_cell45(hl2, d) == "**9**.4 ± 0.5"
    assert report.packs_cell45(hl2, [d]) == "-"


def test_the_board_has_a_pack_points_column(tmp_path, monkeypatch):
    md, _stats, _ = board(tmp_path, monkeypatch, {"m": PASS})
    assert "Pack points" in head(md)
    assert cell(md, "m", "Pack points") == "-"


def test_the_html_row_splits_a_total_with_packs():
    d = {"key": "work", "group": "coding", "in_total": True,
         "parts": [{"key": "a", "weight": 5}, {"key": "b", "weight": 5}]}
    hl = {"S": {"work": 10.7}, "T1": {"work": 9.4}, "m": {"work": 1.0},
          "P": {"work/a": 5.4, "work/b": 5.3}, "unscaled": {"work": {"of": 12.0, "pending": []}}}
    row = {}
    report._section_html45(row, {"headline": hl, "scorecard": {"score_defs": [d]}})
    assert row["sub_tag"]["coding"] == "= 9.4 standard + 1.3 packs"
