"""Fable checkpoint 10: the frozen 4.1 board must not move in its LABELS
either. r2's "[+n packs unrun]" / "[n packs errored]" marks (review B3) are
4.5 board labels (pending_policy / ``scoring_rules: v2``): under ``bench``
they rendered on 7 of 71 rows that 4.1 showed without them. board_ab compared
numbers only, so it let that through; it now compares every score label and
every rendered text field of a board row."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from crucibleforge import report
from crucibleforge.profiles import load_profile

_spec = importlib.util.spec_from_file_location(
    "board_ab", Path(__file__).resolve().parent.parent / "scripts" / "board_ab.py")
board_ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(board_ab)


@pytest.fixture
def every_part_at_95(monkeypatch):
    """Every standard part scores 95 % (>= the 90 % unlock) and no pack has a
    row: each part's first pack is unlocked but unrun."""
    def fake(src, w, partial=frozenset()):
        out = {"chat": None, "coding": None, "total": None, "tok_per_s": None,
               "components": {}, "weights": dict(w)}
        out.update({g: 95.0 for g in w})
        return out
    monkeypatch.setattr(report, "scorecard", fake)


def _card(name: str) -> dict:
    prof = load_profile(name)
    sc = report._declared_weights(prof, None, None)[0]
    # T7: bench45's Main parts list their cases, so each is scored from its own subset of rows
    sub = {f"{sk}/{pk}": {} for sk, sv in (prof.get("scores") or {}).items()
           for pk, p in (sv.get("parts") or {}).items() if (p or {}).get("cases")}
    return report.points_scorecard({"subset_stats": sub} if name == "bench45" else {}, sc, prof)


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
def test_bench_board_never_shows_the_unrun_mark(every_part_at_95):
    card = _card("bench")
    assert card["labels"]["work"] == "9.5"
    assert not any("unrun" in v or "errored" in v for v in card["labels"].values())
    assert not card.get("ladder_unrun") and not card.get("ladder_errored")


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
def test_bench45_board_shows_the_unrun_mark(every_part_at_95):
    card = _card("bench45")
    # A6: work/reasoning is a Frontier part (max 0) that unlocks off the Coding Core score (unlock_part)
    assert "packs unrun]" in card["labels"]["work"]       # 2 without A5's classic packs, more with them
    work = {k: v for k, v in card["ladder_unrun"].items() if k.startswith("work/")}
    assert set(work) == {"work/frontier", "work/code", "work/agentic", "work/reasoning", "work/trace"} and all(v >= 1 for v in work.values())  # more with A5's classic packs; work/trace = the TRACEVM bonus pack (5772360)


def _model(label: str, notes: str | None = "a note", **row) -> dict:
    r = {"label": "m", "rank": 1, "notes": notes, "suite": "4.1", "tok_s": 41.5,
         "components": {"Functions": 81.25, "Chat": 70.0}, "maxed": ["work"],
         "ladder_unrun_total": 0, "cost_usd": 0.0123,
         "wc": {"nsfw/uncensored": {"S": 0.702, "n": 3}}}
    r.update(row)
    return {"total": 9.5, "rank": "1", "labels": {"work": label},
            "parts": {"work": {"code": 3.0}}, "row": r}


def test_board_ab_catches_a_label_only_change():
    res = board_ab.compare({"m": _model("9.5")}, {"m": _model("9.5 [+3 packs unrun]")})
    assert res["changed"] == 1
    assert res["detail"]["m"] == {"label/work": ["9.5", "9.5 [+3 packs unrun]"]}


def test_board_ab_catches_a_row_text_change_and_passes_identical_rows():
    res = board_ab.compare({"m": _model("9.5")}, {"m": _model("9.5", "other note")})
    assert res["detail"]["m"] == {"row/notes": ["a note", "other note"]}
    assert board_ab.compare({"m": _model("9.5")}, {"m": _model("9.5")})["changed"] == 0


def test_board_ab_compares_the_whole_row_recursively():
    """Fable checkpoint 11: a numeric diff deep inside the shipped row counts."""
    nested = _model("9.5", wc={"nsfw/uncensored": {"S": 0.7021, "n": 3}})
    res = board_ab.compare({"m": _model("9.5")}, {"m": nested})
    assert res["detail"]["m"] == {"row/wc/nsfw/uncensored/S": [0.702, 0.7021]}
    comp = _model("9.5", components={"Functions": 81.25, "Chat": 70.5})
    assert board_ab.compare({"m": _model("9.5")}, {"m": comp})["detail"]["m"] == {
        "row/components/Chat": [70.0, 70.5]}
    assert board_ab.compare({"m": _model("9.5")}, {"m": _model("9.5", maxed=["work", "chat"])}
                            )["detail"]["m"] == {"row/maxed[1]": [None, "chat"]}
    assert board_ab.compare({"m": _model("9.5")}, {"m": _model("9.5", cost_usd=0.0124)}
                            )["changed"] == 1
    # float noise below TOL is not a change; an int and an equal float are equal
    assert board_ab.compare({"m": _model("9.5")}, {"m": _model("9.5", tok_s=41.5 + 1e-12)}
                            )["changed"] == 0
    assert board_ab.compare({"m": _model("9.5", tok_s=41)},
                            {"m": _model("9.5", tok_s=41.0)})["changed"] == 0


def test_board_ab_field_only_one_tree_ships():
    base = _model("9.5")
    del base["row"]["ladder_unrun_total"]
    # an empty new field (0 unrun packs) is no change, but is reported
    res = board_ab.compare({"m": base}, {"m": _model("9.5")})
    assert res["changed"] == 0 and res["empty_only_in_one"] == {"row/ladder_unrun_total": 1}
    # a new field with content is a change
    res = board_ab.compare({"m": base}, {"m": _model("9.5", ladder_unrun_total=2)})
    assert res["detail"]["m"] == {"row/ladder_unrun_total": [None, 2]}


def _card_wc(name: str) -> dict:
    prof = load_profile(name)
    sc = report._declared_weights(prof, None, None)[0]
    wc = {"S": 0.7, "W": 1.0, "C": 0.7, "n": 3, "n_engaged": 3}
    s = {"subset_stats": {f"nsfw/{k}": {"nsfw": {"wc": dict(wc)}}
                          for k in ("uncensored", "nsfw_rp", "nsfw_writing")}}
    return report.points_scorecard(s, sc, prof)


def test_bench_row_ships_no_wc_block(every_part_at_95):
    """Fable checkpoint 12: r2's willingness x capability split was computed and
    shipped (html_rows ``wc``) for every 4.1 bench row (whole-row board_ab:
    68 of 71 changed). It is a 4.5 field: bench carries none."""
    card = _card_wc("bench")
    assert report._wc_cells(card) == {}
    assert not any("wc" in (det or {}) for parts in card["ladder"].values()
                   for det in (parts or {}).values())


@pytest.mark.xfail(strict=True, reason="J7: Main NSFW is pending the owner decision, so bench45 declares no willingness case (WL2 left Core) and has no wc cells to ship")
def test_bench45_row_ships_the_wc_block(every_part_at_95):
    cells = report._wc_cells(_card_wc("bench45"))
    assert cells and all(v["n"] == 3 for v in cells.values())


def _rj(**nsfw) -> dict:
    return {"generated": "2026-11-12T03:00:00",
            "models": {"m": {"nsfw": {"willingness": 0.9, "n_unwritten": 0, **nsfw},
                             "scorecard": {"total": 9.5}}},
            "separation": {"pairs": 3}}


def test_board_ab_compares_the_whole_report_json():
    """Fable checkpoint 13: model_stats wrote r2's nsfw.wc into report.json for
    4.1 models; board_ab now compares every leaf of report.json, skipping
    only the VOLATILE write time."""
    base = {"m": _model("9.5"), board_ab.REPORT_JSON: _rj()}
    head = {"m": _model("9.5"),
            board_ab.REPORT_JSON: {**_rj(wc={"S": 0.7, "n": 3}), "generated": "later"}}
    res = board_ab.compare(base, head)
    assert res["changed"] == 0                                   # the board rows agree
    rj = res["report_json"]
    assert rj["changed"] == 2 and rj["by_section"] == {"models/m": 2}
    assert rj["detail"]["models/m/nsfw/wc/S"] == [None, 0.7]
    # a numeric model_stats diff; the write time alone is no change
    head[board_ab.REPORT_JSON] = {**_rj(willingness=0.91), "generated": "later"}
    rj = board_ab.compare(base, head)["report_json"]
    assert rj["detail"] == {"models/m/nsfw/willingness": [0.9, 0.91]}
    head[board_ab.REPORT_JSON] = {**_rj(), "generated": "later"}
    assert board_ab.compare(base, head)["report_json"]["changed"] == 0
    assert board_ab.VOLATILE == {"generated"}


def test_bench_model_stats_carry_no_wc(monkeypatch):
    from crucibleforge import profiles
    for name, want in (("bench", False), ("bench45", True)):
        profiles.set_active(name)
        try:
            assert report._v2_board_fields(None) is want
            assert ("wc" in report.model_stats("m", None, rows=[], meta={})["nsfw"]) is want
        finally:
            profiles.set_active(None)


ALLOW = [{"path": "models/<model>/judge_ids", "kind": "new_key", "control": "pre-r2",
          "reason": "4.5 judge lineage"},
         {"path": "models/<model>/scorecard/ladder/*/*/scoring_mode", "kind": "new_key",
          "control": "r2", "reason": "r2 scoring_mode"},
         {"path": "models/<model>/sets[*]/mode", "kind": "null_to_neutral", "control": "r2",
          "reason": "null -> standard"}]


def _rj2(**model) -> dict:
    m = {"nsfw": {"willingness": 0.9}, "chat_checks": {"rate": 0.774},
         "scorecard": {"ladder": {"chat": {"writing": {"sets": [{"n": 1}]}}}},
         "sets": [{"mode": None}]}
    for k, v in model.items():
        m[k] = v
    return {"generated": "t0", "models": {"a": m, "b": dict(m)}}


def test_report_allow_passes_listed_additive_diffs():
    base = _rj2()
    head = _rj2(judge_ids=["j1", "j2"], sets=[{"mode": "standard"}],
                scorecard={"ladder": {"chat": {"writing": {"sets": [{"n": 1}],
                                                           "scoring_mode": "standard"}}}})
    head["generated"] = "t1"
    res = board_ab.classify_report_json(base, head, ALLOW)
    assert res["n_violations"] == 0, res["violations"]
    assert res["allowed"] == {"models/<model>/judge_ids": 4,
                              "models/<model>/scorecard/ladder/*/*/scoring_mode": 2,
                              "models/<model>/sets[*]/mode": 2}


def test_report_allow_fails_a_numeric_model_stats_change():
    res = board_ab.classify_report_json(_rj2(), _rj2(chat_checks={"rate": 0.793}), ALLOW)
    assert res["n_violations"] == 2 and res["violation_kinds"] == {"value_change": 2}
    assert res["violations"]["models/a/chat_checks/rate"] == [0.774, 0.793, "value_change"]


def test_report_allow_fails_unlisted_and_removed_keys_and_non_neutral_nulls():
    base = _rj2()
    head = _rj2(wc={"S": 0.7}, sets=[{"mode": "wc"}])
    del head["models"]["b"]["nsfw"]
    kinds = board_ab.classify_report_json(base, head, ALLOW)["violation_kinds"]
    assert kinds == {"new_key": 2, "value_change": 2, "removed": 1}


def test_shipped_allow_list_loads_and_every_entry_has_a_reason():
    entries = board_ab.load_allow()
    assert entries and all(e["reason"] and e.get("control") in ("pre-r2", "r2") for e in entries)
    assert not any(e["path"].endswith("chat_checks/rate") for e in entries)
