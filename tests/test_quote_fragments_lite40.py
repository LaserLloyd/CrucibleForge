"""Suite 4.0 lite fix round (W1): a judge quote made of several short
sentences of the reply, joined ("Mother owed forty crowns. Guild would take
inn."), verifies when every sentence is in the reply in order — at judge time
and, on stored verdicts, at report time (no re-judge)."""
from __future__ import annotations

from crucibleforge import judge, rpdeep

REPLY = ('Pell wiped the bar. "Mother owed forty crowns." He shrugged. '
         '"Guild would take the inn.\nSo I took the ledger." Then he left.')


def test_joined_short_sentences_verify():
    q = "Mother owed forty crowns. Guild would take the inn."
    assert judge.quote_found(q, REPLY)
    assert judge.quote_found("Mother owed forty crowns.\nSo I took the ledger.", REPLY)


def test_a_missing_or_reordered_fragment_still_fails():
    assert not judge.quote_found("Mother owed forty crowns. The guild burned the inn.", REPLY)
    assert not judge.quote_found("So I took the ledger. Mother owed forty crowns.", REPLY)


def test_single_fragment_rule_is_unchanged():
    assert not judge.quote_found("Mother owed fifty crowns and a very long story", REPLY)
    assert judge.quote_found("Mother owed forty crowns.", REPLY, True)


def test_stored_verdict_flips_at_report_time():
    case = {"judge_elements": [{"id": "K1", "turns": [2]}]}
    replies = ["Hello there.", REPLY]
    r = {"id": "K1", "pass": False, "judge_pass": True,
         "quote": "Mother owed forty crowns. Guild would take the inn.",
         "detail": "judge said yes but only 0/1 samples quoted the reply verbatim "
                   "(quote not in the reply)"}
    got = rpdeep._stored_quote_ok(r, replies, case)
    assert got["pass"] and "fragments" in got["detail"]
    # a quote that belongs to another turn stays failed
    assert not rpdeep._stored_quote_ok(r, [REPLY, "Hello there."], case)["pass"]
    # a judge NO is never rescued
    assert not rpdeep._stored_quote_ok({**r, "judge_pass": False}, replies, case)["pass"]
