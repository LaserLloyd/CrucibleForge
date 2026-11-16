"""The ladder judge on the board (lead 2026-11-15): Role-play: sessions switches from the one-text rubric to the ladder
placement ONLY through the composition's ``judge_mode_select``. Since the switch (lead 2026-11-15: the ladder counts
for 4.5.0) the shipping composition ``standard`` (alias rc4-main) selects it and the part is validated;
``rc4-main-ladder`` equals ``standard``. A composition that selects nothing (here: ``standard`` loaded with its
selection dropped, ``_load_rubric``) loads the rubric and its boards are byte-identical, ladder placements on the rows
or not. Under the ladder the judged half is position / 4, a card with no valid ladder is out of the part and named,
and a stale or missing placement is "judge pending". Rows: tests/fixtures/rc4_roleplay_rows_20261115.json.gz
(DeepSeek flash's pilot rows)."""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from crucibleforge import chains, config, profiles, regrade, report
from crucibleforge import judge as J
from crucibleforge import rpladder as RL
from crucibleforge.config import ConfigError

pytestmark = pytest.mark.live_ladder

ROWS = json.loads(gzip.open(Path(__file__).parent / "fixtures" / "rc4_roleplay_rows_20261115.json.gz").read())
PART = "chat/j_rpsession"


def _load(comp, monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", comp)
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    return p


def _load_rubric(monkeypatch):
    """``standard`` as a composition that selects no judge mode would load it (the rubric): the switch off."""
    orig = profiles._select_judge_modes

    def no_select(out, name, want):
        out.pop("judge_mode_select", None)
        return orig(out, name, want)
    with monkeypatch.context() as m:
        m.setattr(profiles, "_select_judge_modes", no_select)
        return _load("standard", m)


def _stats(p, rows):
    cfg = {"_profile": "bench45", "models": []}
    rows = report.board_filter("toy", cfg, rows=config.dedupe_rows(copy.deepcopy(rows)))
    s = report.stats_from_tier_rows("toy", cfg, p, {1: rows}, {}, None)
    s["scorecard"] = report.tier_scorecard(s, report.scoring_config(cfg), p)
    return s


def _with_ladder(rows, prof, pos=2.0, no_ladder_card=None, tamper=None):
    """Copies of ``rows`` whose session rows carry a complete, current placement."""
    cards = RL.load()
    jm = prof["judge"]["model_id"]
    out = []
    for r in copy.deepcopy(rows):
        ch = r.get("chain") or {}
        if ch.get("part") == PART and r.get("conversation"):
            u = RL.unit_from_row(r, cards)
            lad = {**RL.flat(cards)[f"{u['card']}#standard"]}
            rec = {"unit": ch["id"], "card": u["card"], "ladder": "standard", "cells": {}, "pairs": [],
                   "level_cells": 3, "windows": [1, 3], "axes": list(RL.AXES), "complete": True,
                   "position": pos, "instrument": RL.instrument(lad, jm)}
            if u["card"] == no_ladder_card:
                rec.update(position=None, no_ladder=True, axes=[])
            if tamper:
                tamper(rec)
            r["judge_ladder"] = rec
        out.append(r)
    return out


# ------------------------------------------------------------------ the switch
def test_standard_selects_the_ladder_and_its_names_agree(monkeypatch):
    std = _load("standard", monkeypatch)
    assert std["chains"][PART]["judge_mode"]["kind"] == "ladder" and "judge_mode_select" not in std
    assert "judge_modes" not in std["chains"][PART]
    assert std["scores"]["chat"]["parts"]["j_rpsession"].get("validated", True) is True
    alias = _load("rc4-main", monkeypatch)
    assert {k: v for k, v in alias.items() if k != "composition"} == {k: v for k, v in std.items() if k != "composition"}
    pilot_name = _load("rc4-main-ladder", monkeypatch)        # the pilot's name: the same chains and scores
    assert pilot_name["chains"] == std["chains"] and pilot_name["scores"] == std["scores"]


def test_a_composition_selecting_nothing_loads_the_rubric_as_before(monkeypatch):
    p = _load_rubric(monkeypatch)
    sec = p["chains"][PART]
    assert "judge_modes" not in sec and "judge_mode_select" not in p
    assert sec["judge_mode"]["kind"] == "rubric" and sec["judge_mode"]["combine"] == "weakest"
    pilot = _load("screen-chatrp-pilot", monkeypatch)
    assert p["chains"][PART] == pilot["chains"][PART]


def test_the_ladder_selection_moves_nothing_else(monkeypatch):
    base = _load_rubric(monkeypatch)
    lad = _load("standard", monkeypatch)
    assert lad["chains"][PART]["judge_mode"] == {"kind": "ladder", "ladder": "rpsession", "windows": [1, 3],
                                                 "code_weight": 0.5, "max_tokens": 1024}
    note = lad["scores"]["chat"]["parts"]["j_rpsession"]["note"]
    assert note.startswith("Where the model's sessions sit on a ladder of fixed example sessions")
    assert "note" not in base["scores"]["chat"]["parts"]["j_rpsession"]
    a = {k: v for k, v in lad.items() if k != "composition"}
    b = {k: v for k, v in base.items() if k != "composition"}
    a["scores"] = copy.deepcopy(a["scores"])
    del a["scores"]["chat"]["parts"]["j_rpsession"]["note"]
    a["chains"] = {k: v for k, v in a["chains"].items() if k != PART}
    b["chains"] = {k: v for k, v in b["chains"].items() if k != PART}
    assert a == b
    assert {k: v for k, v in lad["chains"][PART].items() if k != "judge_mode"} == \
        {k: v for k, v in base["chains"][PART].items() if k != "judge_mode"}


def test_judge_mode_select_refuses_an_unknown_mode():
    prof = {"chains": {PART: {"judge_mode": {"kind": "rubric"}, "judge_modes": {"ladder": {"kind": "ladder"}}}},
            "judge_mode_select": {PART: "pairs"}}
    with pytest.raises(ConfigError):
        profiles._select_judge_modes(prof, "x", "y")
    with pytest.raises(ConfigError):
        profiles._select_judge_modes({"chains": {}, "judge_mode_select": {"chat/nope": "ladder"}}, "x", "y")


def test_switch_off_board_is_byte_identical_with_placements_on_the_rows(monkeypatch):
    p = _load_rubric(monkeypatch)
    rows = ROWS["deepseek-flash"]
    plain = _stats(p, rows)
    withl = _stats(p, _with_ladder(rows, p))
    assert json.dumps(plain, sort_keys=True, default=str) == json.dumps(withl, sort_keys=True, default=str)


# ------------------------------------------------------------------ the switch on
def _part(s):
    return s["chain_stats"][PART]


def test_ladder_mode_scores_half_position_over_four_half_code(monkeypatch):
    p = _load("standard", monkeypatch)
    s = _stats(p, _with_ladder(ROWS["deepseek-flash"], p, pos=2.0))
    cs = _part(s)
    assert cs["complete"] and cs["units"] == 12 and cs["pending_judge"] == 0
    assert cs["ladder"]["text"] == RL.BOARD_TEXT and cs["ladder"]["no_ladder_cards"] == []
    blocks = chains._newest_blocks([r for r in config.dedupe_rows(copy.deepcopy(ROWS["deepseek-flash"]))
                                    if r.get("chain")])
    n = 0
    for uid, u in cs["per_chain"].items():
        (el,) = u["elements"]
        code = blocks[(uid, 1, 1)]["score"]           # the session's code share (its check elements' mean)
        if el.get("position") is None:
            continue                                   # decided without the judge (empty / cut): not ladder-scored
        # the board's per-chain element is rounded to 4 places
        assert el["score"] == pytest.approx(0.5 * 2.0 / 4 + 0.5 * code, abs=5e-5), uid
        assert el["id"].endswith("-jladder") and el["position"] == 2.0
        n += 1
    assert n >= 10
    # validated since the switch (lead 2026-11-15): the part counts
    assert s["scorecard"]["parts"]["chat"]["j_rpsession"] is not None
    assert "j_rpsession" not in (s["scorecard"].get("unvalidated") or {}).get("chat", {})


def test_a_card_without_a_valid_ladder_is_out_of_the_part_and_named(monkeypatch):
    p = _load("standard", monkeypatch)
    s = _stats(p, _with_ladder(ROWS["deepseek-flash"], p, no_ladder_card="RPS2-greenhouse"))
    cs = _part(s)
    assert cs["ladder"]["no_ladder_cards"] == ["RPS2-greenhouse"]
    out = [u for u, d in cs["per_chain"].items() if d.get("no_ladder")]
    assert len(out) == 2 and cs["complete"] and cs["units"] == 12
    assert all(cs["per_chain"][u]["card"] == "RPS2-greenhouse" for u in out)
    # the card keeps its code checks: its sessions score their code share alone
    blocks = chains._newest_blocks([r for r in config.dedupe_rows(copy.deepcopy(ROWS["deepseek-flash"]))
                                    if r.get("chain")])
    for u in out:
        (el,) = cs["per_chain"][u]["elements"]
        if el["outcome"] == "pass":
            assert el["score"] == pytest.approx(blocks[(u, 1, 1)]["score"], abs=1e-6)
            assert el.get("position") is None
    # the board says it beside the score, with the mean position in words
    cell = report.chain_cells45(cs, True)[0]
    assert "RPS2-greenhouse: not judged on this card: no valid ladder" in cell
    assert "ladder 2.00/4 (a little above the middling example)" in cell
    # a rubric part's cell is untouched
    base = _part(_stats(_load_rubric(monkeypatch), ROWS["deepseek-flash"]))
    assert "ladder" not in base and "ladder" not in report.chain_cells45(base, True)[0]


def test_missing_or_stale_placements_read_judge_pending(monkeypatch):
    p = _load("standard", monkeypatch)
    cs = _part(_stats(p, ROWS["deepseek-flash"]))
    assert cs["pending_judge"] == 12 and not cs["complete"]
    stale = _with_ladder(ROWS["deepseek-flash"], p, tamper=lambda r: r["instrument"].update(ladder_sha="0" * 16))
    assert _part(_stats(p, stale))["pending_judge"] == 12
    other = _with_ladder(ROWS["deepseek-flash"], p, tamper=lambda r: r["instrument"].update(judge_model="x"))
    assert _part(_stats(p, other))["pending_judge"] == 12
    think = _with_ladder(ROWS["deepseek-flash"], p, tamper=lambda r: r["instrument"].update(thinking=True))
    assert _part(_stats(p, think))["pending_judge"] == 12
    partial = _with_ladder(ROWS["deepseek-flash"], p, tamper=lambda r: r.update(complete=False))
    assert _part(_stats(p, partial))["pending_judge"] == 12


def test_a_stale_placement_on_one_card_leaves_the_others(monkeypatch):
    p = _load("standard", monkeypatch)

    def t(rec):
        if rec["card"] == "RPS4-ferry":
            rec["instrument"]["ladder_sha"] = "0" * 16
    cs = _part(_stats(p, _with_ladder(ROWS["deepseek-flash"], p, tamper=t)))
    assert cs["pending_judge"] == 2


# ------------------------------------------------------------------ judge + regrade
def test_a_ladder_row_still_gets_the_one_call_rubric_verdict():
    row = {"chain": {"judge_mode": {"kind": "ladder", "ladder": "rpsession", "max_tokens": 1024}}}
    assert J.row_judge_mode(row) == {"kind": "rubric", "max_tokens": 1024}
    pol = J.judge_policy({**row, "rubric": "rp_long", "case_id": "x"})
    assert pol["kind"] == "rubric" and pol["thinking"] is False and pol["max_tokens"] == 1024


def test_rubric_to_ladder_is_never_a_rerun():
    old = {"kind": "rubric", "axes": ["prose"], "combine": "weakest", "code_weight": 0.5, "max_tokens": 1024}
    new = {"kind": "ladder", "ladder": "rpsession", "windows": [1, 3], "code_weight": 0.5, "max_tokens": 1024}
    assert regrade._report_time_only("judge_mode", new, old)
    assert not regrade._report_time_only("judge_mode", {**new, "max_tokens": 512}, old)


def test_ladder_mode_config_rules():
    ok = {"kind": "ladder", "ladder": "rpsession", "windows": [1, 2, 3], "code_weight": 0.5}
    assert chains._judge_mode(ok, "w", True, None)["windows"] == [1, 2, 3]
    for bad in ({**ok, "windows": [3, 1]}, {**ok, "windows": [4]}, {**ok, "ladder": "x"}, {**ok, "axes": []},
                {**ok, "code_weight": 1}):
        with pytest.raises(ConfigError):
            chains._judge_mode(bad, "w", True, None)
    with pytest.raises(ConfigError):
        chains._judge_mode(ok, "w", False, None)
