"""Assistant basics (checked) never enters a total, a rank, a sort or a pack gate (step 3, the proof).

Real rows: the 8B of D's pooled rc4 board (tests/fixtures/rc4_main_ds8b_stats_20261115.json), scored through the real
points scorecard and headline twice, once with the standard profile as it ships (15 more cases in its case lists, the
assistant block, the model's stored assistant rows) and once without the group. Every number the board ranks, sorts
or gates on must be byte-identical."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from crucibleforge import assistant45, headline, profiles, report

FX = Path(__file__).parent / "fixtures" / "rc4_main_ds8b_stats_20261115.json"
ASSIST = {"instruct": ["IZ02-json-single-line-typed", "IZ04-lowercase-except-http", "IZ05-five-dash-lines-exact-26",
                       "IZ06-allcaps-recall-notice", "IZ07-json-city-record", "IZ08-two-questions-only"],
          "reasoning": ["RX02-knights-knaves", "RX08-water-jugs", "RX13-recurrence-term",
                        "RX20-five-houses-four-attributes", "RX21-flawed-cost-chain"],
          "longctx": ["LX2-multihop-16k", "LX3-aggregate-12k", "LX4-count-6k", "LX6-absent-12k"]}
ALL = [i for v in ASSIST.values() for i in v]


@pytest.fixture
def both(monkeypatch):
    monkeypatch.delenv("CF_COMPOSITION", raising=False)
    profiles.set_active("bench45")
    with_ = profiles.load_profile("bench45")
    profiles.activate_cases_root(with_)
    without = copy.deepcopy(with_)
    without.pop("assistant")
    for cat in ASSIST:
        without["cases"].pop(cat)
    return with_, without


def _stats(extra):
    d = json.loads(FX.read_text(encoding="utf-8"))
    s, sc = d["stats"], d["scoring"]
    if extra:
        s = copy.deepcopy(s)
        s["missing_cases"] = sorted(set(s.get("missing_cases") or []) | set(ALL))
        s["assistant45"] = {"status": "ok", "share": 0.1, "band": "weak", "margin": 0.1, "validated": True}
    return s, sc


def _everything_ranked(prof, extra):
    profiles.set_active("bench45")
    s, sc = _stats(extra)
    card = report.points_scorecard(dict(s), sc, prof)
    hl = headline.summarize(card, None, prof)
    card = {k: v for k, v in card.items() if k != "assistant45"}
    return json.dumps({"card": card, "headline": hl}, sort_keys=True, default=str)


def test_overall_totals_ranks_and_pack_gates_are_byte_identical_with_and_without_the_group(both):
    with_, without = both
    assert _everything_ranked(with_, True) == _everything_ranked(without, False)


def test_a_weak_and_a_perfect_assistant_cell_change_nothing_the_board_ranks_on(both):
    with_, _ = both
    base = None
    for share, band in ((0.0, "weak"), (1.0, "pass")):
        profiles.set_active("bench45")
        s, sc = _stats(True)
        s["assistant45"] = {**s["assistant45"], "share": share, "band": band}
        card = report.points_scorecard(dict(s), sc, with_)
        hl = headline.summarize(card, None, with_)
        out = json.dumps({"total": card.get("total"), "parts": card.get("parts"), "ladder": card.get("ladder"),
                          "overall": hl.get("S"), "rank": hl.get("rank"), "class": hl.get("class"),
                          "lean": hl.get("lean")}, sort_keys=True, default=str)
        base = base or out
        assert out == base


def test_the_group_is_in_no_score_definition(both):
    from crucibleforge import tiers
    with_, _ = both
    for d in tiers.scores(with_):
        for p in d.get("parts") or []:
            assert not set(p.get("cases") or []) & set(ALL), (d["key"], p["key"])


def test_the_pack_unlock_reads_main_parts_only(both):
    with_, without = both
    profiles.set_active("bench45")
    s, sc = _stats(True)
    a = report.points_scorecard(dict(s), sc, with_)["ladder"]
    s2, sc2 = _stats(False)
    b = report.points_scorecard(dict(s2), sc2, without)["ladder"]
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
