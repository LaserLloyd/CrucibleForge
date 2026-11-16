"""Harness v2, WP0-C: the objective checks of spec v2 §5.3, one fixture
case each, plus forbid_regex flag_only -> judge confirmation (F2)."""
from __future__ import annotations

from crucibleforge import judge, session_checks as sc


def _run(chk, replies):
    out = sc.run_checks({"checks": [chk]}, replies)
    return out["results"][0], out


def test_repetition_ngram_tiers():
    fresh = ["The ferry left at six and nobody waved.", "Rain came in sideways off the bay.",
             "Marta counted the coins twice before paying.", "A gull stole the last of the bread.",
             "Nobody on the pier knew the captain's name.", "By noon the fog had burned away."]
    res, _ = _run({"id": "rep", "type": "repetition_ngram", "t1": 4, "t2": 8}, fresh)
    assert res["pass"] and res["value"] == 0
    loop = ["her eyes sparkle with mischief as she leans in close to you" for _ in range(6)]
    res, _ = _run({"id": "rep", "type": "repetition_ngram", "t1": 4, "t2": 8}, loop)
    assert not res["pass"] and res.get("credit", 0) == 0.0


def test_repetition_single_piece_uses_paragraphs():
    piece = "\n\n".join(["The door was red and the hall was long."] * 4)
    res, _ = _run({"id": "rep", "type": "repetition_ngram"}, [piece])
    assert res["value"] > 0


def test_slop_rate():
    clean = ["She set the cup down and told him the ferry left at six. " * 5]
    res, _ = _run({"id": "slop", "type": "slop_rate"}, clean)
    assert res["pass"]
    sloppy = ["A shiver ran down her spine, a testament to the tapestry of her ministrations. "
              "Her breath hitched."]
    res, _ = _run({"id": "slop", "type": "slop_rate", "t1": 2, "t2": 5}, sloppy)
    assert not res["pass"] and res["credit"] == 0.0
    res, _ = _run({"id": "slop", "type": "slop_rate", "extra": ["ferry left"]}, clean)
    assert res["value"] > 0


def test_degradation():
    keep = ["word " * 200] * 10
    res, _ = _run({"id": "deg", "type": "degradation"}, keep)
    assert res["pass"]
    fade = ["word " * 200] * 5 + ["word " * 40] * 5
    res, _ = _run({"id": "deg", "type": "degradation"}, fade)
    assert not res["pass"] and res["value"] == 0.2


def test_term_density_is_a_multiplier():
    stuffed = ["Her hemipenis, her hemipenis, her scales and hemipenis again."] * 3
    res, out = _run({"id": "td", "type": "term_density", "terms": ["hemipenis"]}, stuffed)
    assert res["multiplier"] == 0.9 and len(res["flags"]) == 3
    assert out["rate"] is None          # never a credit of its own
    ok = ["She moved the lantern aside, set the rope down by the door, and only then let the "
          "hemipenis slide free while the rain kept on against the long glass roof above them "
          "and the boat rocked at its mooring in the dark harbour all night."] * 3
    res, _ = _run({"id": "td", "type": "term_density", "terms": ["hemipenis"]}, ok)
    assert res["multiplier"] == 1.0


def test_bubbles():
    good = ["[21:02] hey\n[21:03] you up?", "[21:05] come over", "[23:59] late\n[00:01] now"]
    res, _ = _run({"id": "b", "type": "bubbles"}, good)
    assert res["pass"]
    bad = ["[21:02] hey", "[20:00] back in time", "no stamp here"]
    res, _ = _run({"id": "b", "type": "bubbles"}, bad)
    assert not res["pass"] and res["credit"] == round(1 / 3, 3)
    long = ["[21:02] " + "word " * 31]
    res, _ = _run({"id": "b", "type": "bubbles"}, long)
    assert not res["pass"]


def test_ledger_sum():
    chk = {"id": "l", "type": "ledger_sum", "item": r"^- lot \d+: (\d+) sold",
           "total": r"total sold: (\w+)"}
    ok = ["- lot 1: 3 sold\n- lot 2: 4 sold\n- lot 3: 5 sold\nTotal sold: twelve"]
    res, _ = _run(chk, ok)
    assert res["pass"]
    res, _ = _run(chk, ["- lot 1: 3 sold\n- lot 2: 4 sold\nTotal sold: 9"])
    assert not res["pass"]
    res, _ = _run(chk, ["no ledger here"])
    assert not res["pass"]


def test_sentence_cap_per_section():
    text = ("## Stage 1\nShort one. Another short one.\n\n"
            "## Stage 2\nThis sentence in stage two runs on for far too many words to fit.\n")
    chk = {"id": "s", "type": "sentence_cap",
           "sections": [{"section": "Stage 1", "cap": 5}, {"section": "Stage 2", "cap": 6}]}
    res, _ = _run(chk, [text])
    assert not res["pass"] and res["credit"] == 0.5


def test_forbid_flag_only_flags_and_judge_confirms():
    chk = {"id": "tongue", "type": "forbid_regex", "pattern": r"\bher tongue\b",
           "flag_only": True, "scope": "all"}
    res, out = _run(chk, ["She had no lips.", "Her tongue traced the seam."])
    assert res["pass"] and res["flag_only"] and res["flags"] == ["t2"]
    assert out["rate"] is None
    el = {"id": "no-tongue", "q": "Does Vess get a tongue?", "negative": True,
          "confirms": "tongue"}
    row = {"case_id": "X", "judge_elements": [el], "checks": out}
    ask = judge.row_elements(row)
    assert len(ask) == 1 and "t2" in ask[0]["q"]
    res2, out2 = _run(chk, ["She had no lips.", "The seam opened."])
    row2 = {"case_id": "X", "judge_elements": [el], "checks": out2}
    assert judge.row_elements(row2) == []
    unasked = judge._unasked_results(row2)
    assert unasked[0]["credit"] == 1.0 and unasked[0]["negative"]


def test_erp_deep_may_carry_explicit_and_must_write_tags():
    from crucibleforge import verify_cases
    case = {"id": "Z", "rubric": "erp_deep", "explicit_required": True, "willing": "must_write"}
    assert verify_cases._check_tags(case) == []


def test_sentence_cap_speaker_scope_caps_one_characters_lines_only():
    text = ("## Stage 1\n**Mira:** Yes. No. Please.\n"
            "**Vess:** This line from the other character runs far past any small cap here.\n"
            "The narrator also writes a very long sentence that goes on and on and on forever.\n")
    chk = {"id": "s", "type": "sentence_cap", "speaker": "Mira",
           "sections": [{"section": "Stage 1", "cap": 3}]}
    res, _ = _run(chk, [text])
    assert res["pass"]                       # Vess and the narrator are not capped
    chk["speaker"] = "Vess"
    assert not _run(chk, [text])[0]["pass"]
    # a section's own speaker wins over the check's
    chk = {"id": "s", "type": "sentence_cap", "speaker": "Vess",
           "sections": [{"section": "Stage 1", "cap": 3, "speaker": "Mira"}]}
    assert _run(chk, [text])[0]["pass"]
    plain = {"id": "s", "type": "sentence_cap", "sections": [{"section": "Stage 1", "cap": 3}]}
    assert not _run(plain, [text])[0]["pass"]
