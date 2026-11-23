"""Stamp-2 batch G, item A1 (G1) and A6: a multi-turn NSFW+ unit keeps ALL its turns.

The runner writes ``judge_card`` on the final turn row only (and the judge re-appends that row); the rows of the
earlier turns carry none. The fixture is cut from a graded launch folder in exactly that shape (replies replaced by
placeholders of the real length). Before the fix ``units_from_rows`` kept only the card row: a session / dark unit
had ONE reply and an EMPTY scored region (T3 + T4), ground (U4) and probe units were scored on their last turn only.
The same shape in ``nsfwpack45.units_from_rows`` read the card from the group's first row (turn 1).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crucibleforge import classing45 as C, judge, nsfwplus45 as N

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]
ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "stamp2g_runner_rows_20261121.json"
ALL = C.CLASSED_VARIANTS + ("ground", "probe")


@pytest.fixture(autouse=True)
def _no_case_bank_fallback(monkeypatch):
    """``judge.case_field`` falls back to the case bank for a key the row lacks; the real rows must stand alone."""
    monkeypatch.setattr(judge, "_load_case_fields", lambda: None)
    monkeypatch.setattr(judge, "_CASE_FIELDS", {}, raising=False)


def _rows():
    return json.loads(FIX.read_text(encoding="utf-8"))["rows"]


def _by_card(units):
    return {u["card"]: u for u in units if u["card"] != "W2-karth"}


def test_fixture_has_the_runner_shape():
    rows = _rows()
    sess = [r for r in rows if r["case_id"] == "FLAT-a45_sess_s1-002-r1-a1"]
    assert [r["turn"] for r in sess] == [1, 2, 3, 4, 5, 5]
    assert [("judge_card" in r) for r in sess] == [False] * 4 + [True, True]     # card on turn 5 + its re-append
    assert "judge" in sess[-1] and "judge" not in sess[-2]


def test_every_turn_is_kept():
    us = _by_card(C.units_from_rows(_rows(), variants=ALL))
    assert {k: len(u["replies"]) for k, u in us.items()} == {
        "S2-corvin": 5, "D1-reiss-pell": 4, "G2-chat-memory": 3, "P4-corinne": 2}
    for k in ("S2-corvin", "D1-reiss-pell"):
        u = us[k]
        assert u["reply"].strip(), f"{k}: scored region empty"
        assert u["reply"] == "\n\n".join(u["replies"][2:4])           # T3 + T4 (peak turn 3 and the next)
        assert u["region_replies"] == u["replies"][2:4]
    # ground and probe are scored on every turn
    assert us["G2-chat-memory"]["reply"] == "\n\n".join(us["G2-chat-memory"]["replies"])
    assert us["P4-corinne"]["reply"] == "\n\n".join(us["P4-corinne"]["replies"])


def test_unit_takes_the_card_of_the_row_that_carries_it():
    rows = _rows()
    us = _by_card(C.units_from_rows(rows, variants=ALL))
    assert us["S2-corvin"]["variant"] == "session" and us["S2-corvin"]["sexual"] is True
    # a group with no row carrying a card is dropped (never guessed)
    bare = [{k: v for k, v in r.items() if k != "judge_card"} for r in rows]
    assert C.units_from_rows(bare, variants=ALL) == []


def test_nsfwplus_units_hold_every_turn():
    us = {u["card"]: u for u in N.units_from_rows(_rows()) if u["card"] != "W2-karth"}
    assert len(us["S2-corvin"]["replies"]) == 5 and len(us["G2-chat-memory"]["replies"]) == 3
    assert us["S2-corvin"]["classing"]["reply"].strip()


# ---------------------------------------------------------------- A6: pre_read_reason on the scored region
def test_pre_read_judges_the_scored_region_only():
    u = _by_card(C.units_from_rows(_rows(), variants=ALL))["S2-corvin"]
    assert C.unit_pre_read(u) is None
    # a length stop on turn 1 is outside the region: the region is still read
    u1 = {**u, "finishes": ["length"] + u["finishes"][1:]}
    assert C.unit_pre_read(u1) is None
    # an empty region is "empty" even though the other turns hold text
    u2 = {**u, "region_replies": ["", " "], "replies": u["replies"][:2] + ["", " "] + u["replies"][4:]}
    assert C.unit_pre_read(u2) == "empty"
    # a length stop inside the region is "cut"
    u3 = {**u, "region_finishes": ["stop", "length"]}
    assert C.unit_pre_read(u3) == "cut"


def test_empty_region_is_never_sent():
    calls = []

    def chat(reader, messages, **kw):
        calls.append(reader)
        return {"text": '{"class": "SOFTENED"}', "finish": "stop"}
    rd = C.ReaderSpec(name="a", provider="p", model_id="mistralai/x", family="mistral")
    rb = C.ReaderSpec(name="b", provider="p", model_id="kimi-k2.6", family="moonshot")
    with pytest.raises(C.EmptyRegion):
        C.read_one(rd, "ask", "  \n ", chat=chat, prompt=("p", "sha"))
    with pytest.raises(C.EmptyRegion):
        C.class_unit("ask", "", True, "qwen", [rd, rb], C.ReaderStore(None), chat=chat, prompt=("p", "sha"))
    assert calls == []


# ---------------------------------------------------------------- the same shape in the NSFW+ packs
def _pack_stored_rows():
    """A pk_roleplay ``stored`` case (3 user turns) as the runner writes it: the card on the final turn only."""
    from crucibleforge import gen as _gen, ladder
    f = ROOT / "cases" / "ladder" / "nsfw" / "n1_scene" / "set-1.json"
    case = ladder.expand_generated(json.loads(f.read_text(encoding="utf-8")), f)[0]
    m = _gen.materialize_case(dict(case))
    n = len(m["judge_card"].get("turns") or []) or 3
    rows = []
    for t in range(1, n + 1):
        r = {"case_id": case["id"], "repeat": 1, "bench_run_id": "pk1", "turn": t, "response": f"placeholder {t}",
             "finish_reason": "stop", "category": "nsfw", "grader_family": "nsfwpack45",
             "ts": "2026-11-20T00:00:00+00:00"}
        if t == n:
            r["judge_card"] = m["judge_card"]
        rows.append(r)
    return rows, n


def test_pack_unit_takes_the_card_from_the_final_turn():
    from crucibleforge import nsfwpack45 as NP
    rows, n = _pack_stored_rows()
    us = NP.units_from_rows(rows)
    assert len(us) == 1
    u = us[0]
    assert n >= 2 and len(u["replies"]) == n
    assert u["variant"] == "stored" and u["card"] and u["classing"] is not None
    assert u["classing"]["reply"] == "\n\n".join(u["replies"])
