"""Role-play sessions on a ladder of fixed example sessions (crucibleforge/rpladder; lead 2026-11-15, PAIRS's SPEC).

Pure tests on a small synthetic ladder (no rig, no stored rows): both-orders combine, binary placement from R2, the
rung check rule, the verdict cache, the instrument stamp and staleness (a rung edit stales that card only), the
standard pair prompt without the adult page's sentence, and the shipped ladder file (current rows only)."""
from __future__ import annotations

import copy
import json

import pytest

from crucibleforge import rpladder as RL

AX = RL.AXES


def _lad(tag: str, n_turns: int = 9) -> dict:
    turns = [f"{tag} user turn {i + 1}" for i in range(n_turns)]
    rungs = {}
    for k in RL.RUNGS:
        reps = [f"{tag} R{k} reply {i + 1} " + "x" * (10 * k) for i in range(n_turns)]
        rungs[str(k)] = {"replies": reps, "users": turns, "sha": RL.anchor_sha(reps), "fails": [],
                         "source": {"model_id": f"rung-model-{k}"} if k < 4 else {"key": "reference"}}
    lad = {"rungs": rungs}
    lad["ladder_sha"] = RL.ladder_sha_of(lad)
    return {"system": f"You are playing {tag}.", "turns": turns, "candidates": {"standard": lad},
            "order": ["standard"]}


def _cards():
    return {"C1": _lad("C1"), "C2": _lad("C2")}


def _unit(cards, cid, strength: int, model="subject-model", seed=1):
    turns = cards[cid]["turns"]
    return {"card": cid, "users": list(turns), "replies": [f"subject {cid} reply {i} " + "x" * strength
                                                           for i in range(len(turns))],
            "seed": seed, "model": model, "unit": f"{cid}-{model}-s{seed}"}


class LengthJudge:
    """A fake judge: the longer reply text wins ("none" within 2 characters); counts its calls."""

    def __init__(self):
        self.calls = 0

    def __call__(self, reqs):
        out = {}
        for r in reqs:
            self.calls += 1
            body = r["messages"][1]["content"]
            a = body.split("## Text A")[1].split("## Text B")[0]
            b = body.split("## Text B")[1].split("JSON only")[0]
            out[r["key"]] = {"better": "none" if abs(len(a) - len(b)) <= 2 else ("A" if len(a) > len(b) else "B"),
                             "reason": "length"}
        return out


def _all_usable(cards):
    cells = {f"{fk}|{ax}": {"usable": True} for fk in RL.flat(cards) for ax in AX}
    return {"cells": cells, "checked": list(RL.flat(cards))}


# ------------------------------------------------------------------ combine + placement
@pytest.mark.parametrize("sa,sb,v", [("subj", "subj", 1.0), ("anchor", "anchor", 0.0), ("subj", "anchor", .5),
                                     ("anchor", "subj", .5), ("none", "subj", .5), ("none", "none", .5)])
def test_both_orders_combine(sa, sb, v):
    assert RL.combine(sa, sb) == v


def test_side_reads_the_subjects_letter():
    assert RL.side("A", "A") == "subj" and RL.side("B", "A") == "anchor" and RL.side("none", "B") == "none"
    assert RL.side(None, "A") == "none"


@pytest.mark.parametrize("vs,want", [
    ({}, ("ask", 2)), ({2: .5}, ("done", 1.5)),
    ({2: 0.0}, ("ask", 1)), ({2: 0.0, 1: 1.0}, ("done", 1.0)), ({2: 0.0, 1: .5}, ("done", .5)),
    ({2: 0.0, 1: 0.0}, ("done", 0.0)),
    ({2: 1.0}, ("ask", 3)), ({2: 1.0, 3: 0.0}, ("done", 2.0)), ({2: 1.0, 3: .5}, ("done", 2.5)),
    ({2: 1.0, 3: 1.0}, ("ask", 4)), ({2: 1.0, 3: 1.0, 4: 0.0}, ("done", 3.0)),
    ({2: 1.0, 3: 1.0, 4: .5}, ("done", 3.5)), ({2: 1.0, 3: 1.0, 4: 1.0}, ("done", 4.0))])
def test_binary_placement_from_r2(vs, want):
    assert RL.cell_state(vs) == want


@pytest.mark.parametrize("vs,ok", [([1, 1, 1], True), ([1, 1, .5], True), ([1, .5, .5], False),
                                   ([1, 1, 0], False), ([.5, .5, .5], False), ([1, 1, None], False)])
def test_rung_check_step_rule(vs, ok):
    assert RL.step_ok([None if v is None else float(v) for v in vs]) is ok


def test_windows_are_one_to_three_four_to_six_seven_to_end():
    assert RL.windows(10) == [[0, 1, 2], [3, 4, 5], [6, 7, 8, 9]]
    assert RL.DEFAULT_WINDOWS == (1, 3) and RL.CHECK_WINDOWS == (1, 2, 3)


def test_level_names():
    assert RL.level_name(1.5) == "level with the middling example"
    assert RL.level_name(2.1) == "a little below the strong example" or RL.level_name(2.1).endswith("middling example")
    assert RL.level_name(1.8) == "a little above the middling example"
    assert RL.level_name(2.0) == "a little above the middling example"     # a midpoint reads from the lower rung
    assert RL.level_name(2.3) == "a little below the strong example"
    assert RL.level_name(0.0) == "below the weak example"
    assert RL.level_name(4.0) == "above the house reference"


# ------------------------------------------------------------------ prompt
def test_standard_pair_prompt_has_no_adult_sentence_and_prototype_has():
    m = RL.pair_messages("ctx", "aa", "bb", "prose")
    p = RL.pair_messages("ctx", "aa", "bb", "prose", wording="prototype")
    assert "adult" not in m[1]["content"].lower() and "sex" not in m[1]["content"].lower()
    assert "Every character is an adult" in p[1]["content"]
    assert RL.pair_prompt_sha("standard") != RL.pair_prompt_sha("prototype")
    from crucibleforge import judge as J
    assert m[0]["content"] == J.JUDGE_SYSTEM_45
    assert J._INJECT_GUARD in m[1]["content"] and "PROSE:" in m[1]["content"]


def test_axis_lines_are_the_rubric_lines():
    from crucibleforge import judge as J
    for ax, line in RL.axis_lines().items():
        assert line.startswith(f"- {ax.upper()}: ") and line.split("\n")[0] in J.RP_LONG_TEXT


def test_a_rubric_line_edit_moves_the_pair_prompt_sha(monkeypatch):
    from crucibleforge import judge as J
    before = RL.pair_prompt_sha()
    monkeypatch.setattr(J, "RP_LONG_TEXT", J.RP_LONG_TEXT.replace("no filler", "no padding"))
    assert RL.pair_prompt_sha() != before


def test_request_key_separates_orders_and_judges():
    cards = _cards()
    lad = RL.selected(cards, _all_usable(cards))["C1"]
    u = _unit(cards, "C1", 30)
    a, b = RL.pair_requests(lad, u, 1, "prose", 2, lad["rungs"]["2"]["replies"], "standard", "judge-x")
    assert a["subj_is"] == "A" and b["subj_is"] == "B" and a["key"] != b["key"]
    a2, _ = RL.pair_requests(lad, u, 1, "prose", 2, lad["rungs"]["2"]["replies"], "standard", "judge-y")
    assert a2["key"] != a["key"]


# ------------------------------------------------------------------ placement end to end
def test_place_units_walks_the_ladder_and_the_cache_makes_a_rerun_free(tmp_path):
    cards = _cards()
    chk = _all_usable(cards)
    cache = RL.VerdictCache(tmp_path / "v.jsonl")
    judge = LengthJudge()
    # 'subject C1 reply i ' + x*22 (41 chars) vs 'C1 Rk reply i ' + x*10k: beats R2 (34), loses R3 (44) -> 2.0
    units = [_unit(cards, "C1", 22), _unit(cards, "C2", 5)]
    got = RL.place_units(units, cards, judge, cache, judge_model="judge-x", check=chk, windows_used=(1, 3))
    p1 = got[units[0]["unit"]]
    assert p1["complete"] and p1["position"] == 2.0 and len(p1["cells"]) == 8
    assert {pr["rung"] for pr in p1["pairs"]} == {2, 3}
    assert all(pr["subj_is"] in "AB" and pr["better"] in ("A", "B", "none") for pr in p1["pairs"])
    p2 = got[units[1]["unit"]]
    assert p2["position"] == 0.5            # 24 chars: loses R2 (34), level with R1 (24)
    ins = p1["instrument"]
    assert ins["mode"] == "ladder" and ins["thinking"] is False and ins["judge_model"] == "judge-x"
    assert ins["ladder_sha"] == cards["C1"]["candidates"]["standard"]["ladder_sha"]
    assert ins["pair_prompt_sha"] == RL.pair_prompt_sha()
    n = judge.calls
    assert n > 0
    # a re-run (same cache file, re-read) asks nothing and places the same
    again = RL.place_units(units, cards, judge, RL.VerdictCache(tmp_path / "v.jsonl"), judge_model="judge-x",
                           check=chk, windows_used=(1, 3))
    assert judge.calls == n and again[units[0]["unit"]]["position"] == 2.0
    # another judge model: nothing reused
    RL.place_units(units, cards, judge, cache, judge_model="judge-y", check=chk, windows_used=(1, 3))
    assert judge.calls > n


def test_three_windows_ask_twelve_cells():
    cards = _cards()
    got = RL.place_units([_unit(cards, "C1", 45)], cards, LengthJudge(), RL.VerdictCache(None),
                         judge_model="j", check=_all_usable(cards), windows_used=(1, 2, 3))
    p = next(iter(got.values()))
    assert len(p["cells"]) == 12 and p["position"] == 4.0          # longer than every rung incl. R4 (x*40)


def test_a_card_axis_that_fails_the_rung_check_is_skipped_for_every_model():
    cards = _cards()
    chk = _all_usable(cards)
    for ax in ("dialogue", "atmosphere"):
        chk["cells"][f"C1#standard|{ax}"]["usable"] = False
    for ax in AX:
        chk["cells"][f"C2#standard|{ax}"]["usable"] = False
    units = [_unit(cards, "C1", 25, model=m) for m in ("m1", "m2")] + [_unit(cards, "C2", 25, model="m1")]
    got = RL.place_units(units, cards, LengthJudge(), RL.VerdictCache(None), judge_model="j", check=chk)
    for u in units[:2]:
        p = got[u["unit"]]
        assert p["axes"] == ["prose", "emotion"] and {c.split("|")[1] for c in p["cells"]} == {"prose", "emotion"}
        assert not any(pr["axis"] in ("dialogue", "atmosphere") for pr in p["pairs"])
    p = got[units[2]["unit"]]
    assert p["no_ladder"] and p["position"] is None and p["pairs"] == []


def test_a_card_waits_while_its_rung_check_is_missing():
    cards = _cards()
    chk = _all_usable(cards)
    chk["checked"] = ["C1#standard"]
    got = RL.place_units([_unit(cards, "C2", 25)], cards, LengthJudge(), RL.VerdictCache(None), judge_model="j",
                         check=chk)
    p = next(iter(got.values()))
    assert p["complete"] is False and p["waiting"] == "rung check"


def test_the_session_that_is_the_rung_meets_its_other_seed():
    # fix round 2026-11-18 (REVIEW finding 3): keyed on the identical reply sha, no longer on the model id; a rung
    # model's other sessions meet the frozen rung text (tests/test_s3_ladder_frozen_rung_20261118.py)
    cards = _cards()
    me = _unit(cards, "C1", 25, model="rung-model-2", seed=1)
    me["replies"] = list(cards["C1"]["candidates"]["standard"]["rungs"]["2"]["replies"])
    other = _unit(cards, "C1", 25, model="rung-model-2", seed=2)
    other["replies"] = [r + "yy" for r in other["replies"]]
    got = RL.place_units([me], cards, LengthJudge(), RL.VerdictCache(None), judge_model="j",
                         check=_all_usable(cards), own_other=lambda u: other)
    p = got[me["unit"]]
    r2 = [pr for pr in p["pairs"] if pr["rung"] == 2]
    assert r2 and all(pr["anchor_sha"] == RL.anchor_sha(other["replies"]) for pr in r2)
    # without the other seed the cell waits
    got = RL.place_units([me], cards, LengthJudge(), RL.VerdictCache(None), judge_model="j",
                         check=_all_usable(cards), own_other=lambda u: None)
    assert got[me["unit"]]["complete"] is False


# ------------------------------------------------------------------ rung check
def test_rung_check_on_all_three_windows_and_r4_never_gates(tmp_path):
    cards = _cards()
    judge = LengthJudge()
    cache = RL.VerdictCache(None)
    chk = RL.rung_check(cards, judge, cache, judge_model="j")
    assert chk["complete"] and sorted(chk["checked"]) == ["C1#standard", "C2#standard"]
    assert judge.calls == 2 * 3 * 4 * 3 * 2                         # cards x steps x axes x windows x orders
    cell = chk["cells"]["C1#standard|prose"]
    assert cell["steps"]["R2>R1"] == [1.0, 1.0, 1.0] and cell["usable"]
    # R4 losing to R3 does not gate
    c2 = copy.deepcopy(cards)
    lad = c2["C1"]["candidates"]["standard"]
    lad["rungs"]["4"]["replies"] = ["short"] * 9
    lad["rungs"]["4"]["sha"] = RL.anchor_sha(lad["rungs"]["4"]["replies"])
    lad["ladder_sha"] = RL.ladder_sha_of(lad)
    chk2 = RL.rung_check(c2, LengthJudge(), RL.VerdictCache(None), judge_model="j")
    assert chk2["cells"]["C1#standard|prose"]["steps"]["R4>R3"] == [0.0, 0.0, 0.0]
    assert chk2["cells"]["C1#standard|prose"]["usable"]
    # cached per ladder: a rung edit on C1 leaves C2's check in place
    RL.save_check(tmp_path, cards, chk, "j")
    loaded = RL.load_check(tmp_path, c2, "j")
    assert loaded["checked"] == ["C2#standard"]


def test_rung_check_fails_when_r2_does_not_beat_r1():
    cards = _cards()
    lad = cards["C1"]["candidates"]["standard"]
    lad["rungs"]["1"]["replies"] = [r + "x" * 30 for r in lad["rungs"]["1"]["replies"]]
    lad["rungs"]["1"]["sha"] = RL.anchor_sha(lad["rungs"]["1"]["replies"])
    lad["ladder_sha"] = RL.ladder_sha_of(lad)
    chk = RL.rung_check(cards, LengthJudge(), RL.VerdictCache(None), judge_model="j")
    assert not chk["cells"]["C1#standard|prose"]["usable"] and chk["cells"]["C2#standard|prose"]["usable"]


def test_the_candidate_with_more_usable_axes_is_the_cards_ladder():
    cards = _cards()
    alt = copy.deepcopy(cards["C1"]["candidates"]["standard"])
    alt["rungs"]["2"]["replies"] = [r + "z" for r in alt["rungs"]["2"]["replies"]]
    alt["rungs"]["2"]["sha"] = RL.anchor_sha(alt["rungs"]["2"]["replies"])
    alt["ladder_sha"] = RL.ladder_sha_of(alt)
    cards["C1"]["candidates"]["alt"] = alt
    cards["C1"]["order"] = ["standard", "alt"]
    chk = _all_usable(cards)
    chk["cells"]["C1#standard|prose"]["usable"] = False
    assert RL.selected(cards, chk)["C1"]["candidate"] == "alt"
    chk["cells"]["C1#standard|prose"]["usable"] = True
    assert RL.selected(cards, chk)["C1"]["candidate"] == "standard"          # a tie keeps the listed order
    chk["checked"] = ["C1#standard", "C2#standard"]
    assert RL.selected(cards, chk)["C1"] is None                              # alt not checked yet: wait


# ------------------------------------------------------------------ staleness
def test_a_rung_edit_stales_that_card_only():
    cards = _cards()
    chk = _all_usable(cards)
    units = [_unit(cards, "C1", 25), _unit(cards, "C2", 25)]
    got = RL.place_units(units, cards, LengthJudge(), RL.VerdictCache(None), judge_model="j", check=chk)
    recs = [got[u["unit"]] for u in units]
    assert [RL.stale_reason(r, cards, chk, "j") for r in recs] == [None, None]
    c2 = copy.deepcopy(cards)
    lad = c2["C1"]["candidates"]["standard"]
    lad["rungs"]["3"]["replies"] = [r + "!" for r in lad["rungs"]["3"]["replies"]]
    lad["rungs"]["3"]["sha"] = RL.anchor_sha(lad["rungs"]["3"]["replies"])
    lad["ladder_sha"] = RL.ladder_sha_of(lad)
    assert [RL.stale_reason(r, c2, chk, "j") for r in recs] == ["ladder", None]


def test_prompt_judge_or_thinking_changes_stale_every_card(monkeypatch):
    cards = _cards()
    chk = _all_usable(cards)
    got = RL.place_units([_unit(cards, "C1", 25)], cards, LengthJudge(), RL.VerdictCache(None), judge_model="j",
                         check=chk)
    rec = next(iter(got.values()))
    assert RL.stale_reason(rec, cards, chk, "other-judge") == "judge"
    bad = copy.deepcopy(rec)
    bad["instrument"]["thinking"] = True
    assert RL.stale_reason(bad, cards, chk, "j") == "thinking"
    from crucibleforge import judge as J
    monkeypatch.setattr(J, "JUDGE_SYSTEM_45", J.JUDGE_SYSTEM_45 + " ")
    assert RL.stale_reason(rec, cards, chk, "j") == "pair_prompt"


# ------------------------------------------------------------------ the shipped ladder file
def test_the_shipped_ladder_file_verifies_and_holds_current_publishable_rows_only():
    cards = RL.load()
    from crucibleforge.gen import rpsession as G
    bank = {c["id"]: c for c in G.load_bank()["cards"]}
    assert set(cards) == set(bank)
    for cid, c in cards.items():
        assert c["turns"] == list(bank[cid]["turns"]) and c["system"] == bank[cid]["system"]
        assert c["order"][0] == "standard"
        for name, lad in c["candidates"].items():
            assert lad["rungs"]["4"]["replies"] == list(bank[cid]["reference"])
            for k in ("1", "2", "3"):
                r = lad["rungs"][k]
                assert r["users"] == c["turns"]                          # answered today's script
                assert not any(x.startswith(("voice", "nopuppet", "empty-turn")) for x in r["fails"])
                assert not r["source"]["label"].startswith("cc-") and "claude" not in r["source"]["model_id"]
        std = c["candidates"]["standard"]["rungs"]
        assert [std[k]["source"]["key"] for k in ("1", "2", "3")] == ["grok", "ref", "dsflash"]


def test_a_tampered_rung_text_is_refused(tmp_path):
    d = json.loads(RL.DATA.read_text(encoding="utf-8"))
    cid = sorted(d["cards"])[0]
    d["cards"][cid]["candidates"]["standard"]["rungs"]["2"]["replies"][0] += " (edited)"
    p = tmp_path / "l.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(RL.LadderError):
        RL.load(p)
