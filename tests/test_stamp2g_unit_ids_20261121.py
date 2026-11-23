"""Stamp-2 batch G, item A2 (G2): a unit id carries its seed set.

The written / dwell / session / dark cards run in two seed sets (case ids ``a45_write_s1-...`` and ``..._s2-...``)
with repeat 1 each. The id was ``card|r<repeat>|run``, so the two seed sets of one card shared an id and the later
one's verdict overwrote the earlier one's (``out[u["id"]] = verdict``). Rows cut from the graded heretic folder
(W2-karth s1 + s2, placeholders of the real length).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crucibleforge import classing45 as C, judge, nsfwpack45 as NP, nsfwplus45 as N

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]
ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "stamp2g_runner_rows_20261121.json"
SHA = C.load_prompt({})[1]
RA = C.ReaderSpec(name="RA", provider="p", model_id="mistralai/mistral-large-4-0", family="mistral")
RB = C.ReaderSpec(name="RB", provider="p", model_id="kimi-k2.6", family="moonshot")


@pytest.fixture(autouse=True)
def _no_case_bank_fallback(monkeypatch):
    monkeypatch.setattr(judge, "_load_case_fields", lambda: None)
    monkeypatch.setattr(judge, "_CASE_FIELDS", {}, raising=False)


def _rows():
    return json.loads(FIX.read_text(encoding="utf-8"))["rows"]


def test_seed_set_of_case_id():
    assert C.seed_set_of("FLAT-a45_write_s1-002-r1-a1") == 1
    assert C.seed_set_of("FLAT-a45_write_s2-002-r1-a1") == 2
    assert C.seed_set_of("FLAT-a45_sess_s2-007-r1-a1") == 2
    assert C.seed_set_of("FLAT-a45_taboo-003-r1-a1") == 1          # one seed set: 1
    assert C.seed_set_of("PKW-r1-1") == 1


def test_two_seed_sets_two_ids():
    us = [u for u in C.units_from_rows(_rows()) if u["card"] == "W2-karth"]
    assert len(us) == 2
    assert sorted(u["unit_id"] for u in us) == ["W2-karth|s1|r1|903d7a99", "W2-karth|s2|r1|903d7a99"]
    assert sorted(u["seed_set"] for u in us) == [1, 2]
    ids = [u["unit_id"] for u in C.units_from_rows(_rows(), variants=C.CLASSED_VARIANTS + ("ground", "probe"))]
    assert len(ids) == len(set(ids))


def _store_for(us):
    """An in-memory store: EXPLICIT (with a quote taken from the reply) for s1, SOFTENED for s2."""
    st = C.ReaderStore(None)
    for u in us:
        sha = C.unit_sha(u["ask"], u["reply"])
        for rd in (RA, RB):
            if u["seed_set"] == 1:
                v = {"cls": "EXPLICIT", "quote": " ".join(u["reply"].split()[:5]), "quote_ok": True}
            else:
                v = {"cls": "SOFTENED", "quote": "", "quote_ok": False}
            st.put(sha, rd, SHA, {**v, "raw": "", "finish": "stop", "usage": None, "reason": None})
    return st


def test_each_seed_set_gets_its_own_verdict(monkeypatch):
    # the reader quote check needs a crude word near the quote; the placeholder text has none, so the quote rule is
    # stubbed to accept (the test is about which verdict reaches which unit, not about the quote rule)
    monkeypatch.setattr(C, "verify_quote", lambda q, r, s: True)
    nus = [u for u in N.units_from_rows(_rows()) if u["card"] == "W2-karth"]
    cus = [u for u in C.units_from_rows(_rows()) if u["card"] == "W2-karth"]
    st = _store_for(cus)
    got = {}
    for u in nus:
        cl = u["classing"]
        got[u["id"]] = C.class_unit(cl["ask"], cl["reply"], cl["sexual"], "qwen", [RA, RB], st, prompt=("p", SHA),
                                    chat=C.offline_chat)["cls"]
    assert got == {"W2-karth|s1|r1|903d7a99": "EXPLICIT", "W2-karth|s2|r1|903d7a99": "SOFTENED"}


def test_duplicate_ids_raise():
    rows = [r for r in _rows() if r["case_id"] == "FLAT-a45_write_s1-002-r1-a1"]
    # the same card under a second case id of the same seed set and run: two units, one id
    twin = [{**r, "case_id": "FLAT-a45_write_s1-009-r1-a1"} for r in rows]
    with pytest.raises(C.DuplicateUnitId):
        C.units_from_rows(rows + twin)


def test_pack_duplicate_ids_raise():
    from crucibleforge import gen as _gen, ladder
    f = ROOT / "cases" / "ladder" / "nsfw" / "n7_taboo" / "set-1.json"
    case = ladder.expand_generated(json.loads(f.read_text(encoding="utf-8")), f)[0]
    m = _gen.materialize_case(dict(case))
    r = {"case_id": case["id"], "repeat": 1, "bench_run_id": "pk1", "turn": 1, "response": "placeholder",
         "finish_reason": "stop", "grader_family": "nsfwpack45", "judge_card": m["judge_card"], "ts": "1"}
    assert len(NP.units_from_rows([r])) == 1
    # a second pack case id carrying the same card
    m2 = case["id"].rsplit("-", 1)[0] + "-9"
    with pytest.raises(C.DuplicateUnitId):
        NP.units_from_rows([r, {**r, "case_id": m2}])


def test_reader_meta_carries_the_seed_set():
    src = (ROOT / "scripts" / "read45.py").read_text(encoding="utf-8")
    assert '"seed_set": u["seed_set"]' in src
