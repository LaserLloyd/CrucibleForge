"""Harness v2, RP skeleton needs: negative elements pass on NO, clock
elements at half credit, OOC probe turns exempt from the format gate, quotes
verified against the probe turns, the report-only speaker_spread check, and
lorebook depth (measured in the lint; a card that would be clamped goes the
window path instead of being cut)."""
from __future__ import annotations

from crucibleforge import judge, lint, report, rpdeep, session_checks as sc

T1 = "\"The tide turns at dusk,\" Vess says, coiling the rope."
T2 = "\"Bring the lamp,\" Vess says. \"The ward-stone wakes at the third bell.\""


def _convo(*replies, system="CARD"):
    c = [{"role": "system", "content": system}]
    for i, r in enumerate(replies, 1):
        c += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": r}]
    return c


def test_negative_element_passes_on_no_and_fails_on_a_verified_yes():
    row = {"conversation": _convo(T1, T2)}
    neg = {"id": "disclaimer", "q": "Is there a disclaimer?", "negative": True}
    out = judge.apply_elements(row, {}, [neg], {"disclaimer": {"pass": False, "quote": ""}},
                               "erp_deep")
    r = out["results"][0]
    assert r["pass"] and not r["found"] and r["credit"] == 1.0 and out["rate"] == 1.0
    out = judge.apply_elements(row, {}, [neg], {"disclaimer": {"pass": True,
                                                               "quote": "The tide turns at dusk"}},
                               "erp_deep")
    r = out["results"][0]
    assert not r["pass"] and r["found"] and r["credit"] == 0.0 and out["rate"] == 0.0
    # wc_credit agrees with the stored credit and with pass
    assert report.wc_credit({"judge": {"elements": {"results": [{"id": "x", "pass": True,
                                                                  "negative": True}]}}})["C"] == 1.0


def test_clock_element_is_half_credit_for_one_step_off():
    e = {"id": "bell", "q": "Is the ward-stone timed right?", "clock": True}
    assert judge.element_scale(e) == 2
    assert judge.element_credit(e, True, 1) == 0.5
    assert judge.element_scale({"id": "k", "kind": "clock"}) == 2
    row = {"conversation": _convo(T1, T2)}
    out = judge.apply_elements(row, {}, [e], {"bell": {"pass": True, "score": 1,
                                                       "quote": "at the third bell"}}, "erp_deep")
    assert out["results"][0]["credit"] == 0.5


def test_quotes_are_checked_against_the_probe_turns():
    row = {"conversation": _convo(T1, T2)}
    probe = {"id": "p", "q": "Does she recall the tide at turn 2?", "turns": [2]}
    # the quote is real but from turn 1: it does not prove the turn-2 probe
    out = judge.apply_elements(row, {}, [probe], {"p": {"pass": True,
                                                        "quote": "The tide turns at dusk"}},
                               "erp_deep")
    assert not out["results"][0]["pass"]
    out = judge.apply_elements(row, {}, [probe], {"p": {"pass": True,
                                                        "quote": "The ward-stone wakes"}},
                               "erp_deep")
    assert out["results"][0]["pass"]
    # a windowed sub-row keeps live turn numbers in its user lines
    sub = {"conversation": [{"role": "user", "content": "(live turn 7) u"},
                            {"role": "assistant", "content": T1},
                            {"role": "user", "content": "(live turn 8) u"},
                            {"role": "assistant", "content": T2}]}
    assert judge.element_quote_text(sub, {"turns": [8]}) == T2
    assert judge.element_quote_text(sub, {"turns": [99]}) == judge.assistant_text(sub)
    # the sampled path verifies against the probe turns too
    agg = judge._aggregate_elements([{"elements": {"p": {"pass": True,
                                                         "quote": "The tide turns at dusk"}}}],
                                    [probe], judge.assistant_text(row), row)
    assert not agg["p"]["pass"]


def test_ooc_probe_turns_are_exempt_from_the_format_gate():
    case = {"id": "R", "turns": ["hello", "((OOC: what is Vess's rule on the lamp?))", "go on"],
            "judge_elements": [{"id": "F1-e", "q": "?", "turns": [2], "fact": "F1"}],
            "checks": [{"id": "fmt", "type": "forbid_regex", "pattern": r"^\s*-\s", "ftype": "format",
                        "flag_only": False}]}
    assert sc.ooc_probe_turns(case) == {2}
    assert sc.ooc_probe_turns({"turns": ["a"], "ooc_turns": [1]}) == {1}
    ooc_answer = "OOC: her rule is\n- lamp lit at dusk\n- never past the third bell"
    row = {"conversation": _convo(T1, ooc_answer, T2),
           "judge": {"elements": {"results": [{"id": "F1-e", "pass": True, "credit": 1.0}]}}}
    res = rpdeep.score_row(row, case)
    item = next(i for i in res["items"] if i["id"] == "F1-e")
    assert item["pass"], item
    assert 2 not in (res.get("ooc_turns") or {})
    # the format check skipped the OOC turn
    chk = sc.run_checks(case, [T1, ooc_answer, T2])
    assert chk["results"][0]["pass"]
    # without the OOC marker the same reply fails the gate
    case2 = dict(case, turns=["hello", "what is her rule?", "go on"])
    res2 = rpdeep.score_row(row, case2)
    assert not next(i for i in res2["items"] if i["id"] == "F1-e")["pass"]


def test_speaker_spread_reports_and_scores_nothing():
    text = ("Vess: \"Hold the lamp.\"\nOrrin: \"I hold it.\"\n\n"
            "Vess turned. \"Higher, higher still, past the ward-stone.\"")
    out = sc.run_checks({"checks": [{"id": "sp", "type": "speaker_spread",
                                     "speakers": ["Vess", "Orrin"]},
                                    {"id": "w", "type": "forbid_regex", "pattern": "zzz"}]},
                        [text])
    sp = out["results"][0]
    assert sp["report_only"] and sp["pass"]
    v = sp["value"]
    assert v["words"]["Vess"] > v["words"]["Orrin"] > 0 and 0 < v["entropy"] < 1
    assert out["rate"] == 1.0 and len([r for r in out["results"] if sc.scores_itself(r)]) == 1
    r = {"checks": out, "judge": {"elements": {"results": []}}}
    assert report.wc_credit(r)["n_checks"] == 1


def _lore_case(card_words=3200, at=3050):
    words = ["w"] * card_words
    words[at:at + 6] = "the lamp is never lit indoors".split()
    return {"id": "LR1-x", "system": " ".join(words), "turns": ["t"] * 12,
            "facts": [{"id": "L1", "kind": "lorebook", "text": "the lamp is never lit indoors",
                       "trigger_turn": 11}],
            "judge_elements": [{"id": "L1-e", "fact": "L1", "turns": [11]}]}


def test_lorebook_depth_is_measured_and_a_moved_anchor_is_a_problem():
    probs, notes = lint.lorebook_report(_lore_case())
    assert probs == [] and "L1 at word 3,050 of 3,200, triggered turn 11" in notes[0]
    case = _lore_case()
    case["facts"][0]["anchor"] = "the lamp burns forever"
    probs, _ = lint.lorebook_report(case)
    assert any("not found in the card" in p for p in probs)
    probs, _ = lint.lorebook_report(_lore_case(), ctx_tokens=4096)
    assert any("card's tail would be lost" in p for p in probs)


class _JC:
    context_length = 8192
    max_tokens = 2048


def test_a_card_that_would_be_clamped_goes_the_window_path():
    card = " ".join(["lore"] * 6000) + " the lamp is never lit indoors"
    row = {"rubric": "erp_session", "conversation": _convo(T1, T2, system=card),
           "case_id": "LR1-x", "v2": True}
    assert judge.card_would_clamp(_JC(), row)
    # fix pass #9: an existing (non-v2) row keeps its old single-prompt regime
    assert not judge.card_would_clamp(_JC(), dict(row, v2=False))
    short = dict(row, conversation=_convo(T1, T2, system="short card"))
    assert not judge.card_would_clamp(_JC(), short)
    # a sub-row or a reply-only overflow keeps the old path
    assert not judge.card_would_clamp(_JC(), dict(row, long_window=True))
    huge = dict(row, conversation=_convo("x " * 40000, T2, system="short card"))
    assert not judge.card_would_clamp(_JC(), huge)
