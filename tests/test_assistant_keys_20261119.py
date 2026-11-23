"""Assistant basics (checked), step 2: every key proven by execution.

For each of the 15 items the reference answer goes through the real checker and earns full credit; at least two
plausible wrong answers (a wrong value, the right format with the wrong content, a refusal, an empty reply, the answer
only inside a reasoning block) earn none. The reasoning items' ``verify`` expressions are executed too, so a key that
no longer matches its derivation is caught here and not on the rig.

Not changed: the reasoning-channel fallback for a finished reply (a stamped board policy, 2026-11-13).

Fixed here (keys that accepted a wrong answer): LX3 took the three badge numbers in any order although the question
asks for them in that order (now one ordered pattern); LX6 counted any "does not" in the answer line as "not stated", so a fabricated value with a
negation passed (the loose negations are gone; the prompt itself asks for 'Answer: not stated')."""
from __future__ import annotations

import pytest

from crucibleforge import graders, verify_cases
from crucibleforge.api import ChatResult
from crucibleforge.config import load_cases

IDS = ["IZ02-json-single-line-typed", "IZ04-lowercase-except-http", "IZ05-five-dash-lines-exact-26",
       "IZ06-allcaps-recall-notice", "IZ07-json-city-record", "IZ08-two-questions-only",
       "RX02-knights-knaves", "RX08-water-jugs", "RX13-recurrence-term", "RX20-five-houses-four-attributes",
       "RX21-flawed-cost-chain", "LX2-multihop-16k", "LX3-aggregate-12k", "LX4-count-6k", "LX6-absent-12k"]

# (reference answer, [plausible wrong answers with a name])
KEYS = {
    "IZ02-json-single-line-typed": (
        '{"title": "Harbor Notes", "count": 3, "ready": true, "tags": ["port", "log"]}',
        {"count as a string": '{"title": "Harbor Notes", "count": "3", "ready": true, "tags": ["port", "log"]}',
         "code fence": '```json\n{"title": "Harbor Notes", "count": 3, "ready": true, "tags": ["port"]}\n```',
         "forbidden word": '{"title": "Harbor of the north", "count": 3, "ready": true, "tags": []}'}),
    "IZ04-lowercase-except-http": (
        "checked the logs again and found that every request returned 200 over HTTP without any delay. then the "
        "retry loop also stopped, so nothing else waits now today.",
        {"29 words": "checked the logs again and found that every request returned 200 over HTTP without any delay. "
                     "then the retry loop also stopped, so nothing else waits now today again.",
         "capital C": "Checked the logs again and found that every request returned 200 over HTTP without any delay. "
                      "then the retry loop also stopped, so nothing else waits now today."}),
    "IZ05-five-dash-lines-exact-26": (
        "- pack light and fold every shirt flat\n- pack shoes inside one bag\n- roll socks into small gaps\n"
        "- carry one charger only\n- pack early and check twice!",
        {"four lines": "- pack light and fold every shirt flat\n- pack shoes inside one bag\n"
                       "- roll socks into small gaps and carry one charger only\n- pack early and check twice!",
         "a full stop": "- pack light and fold every shirt flat.\n- pack shoes inside one bag\n"
                        "- roll socks into small gaps\n- carry one charger only\n- pack early and check twice!"}),
    "IZ06-allcaps-recall-notice": (
        "RECALL: OWNERS OF BATTERY MODEL 4400 MUST STOP USING THE DEVICE AND SHOULD ALSO RETURN THE BATTERY TO THE "
        "LOCAL STORE IMMEDIATELY.",
        {"lower case": "Recall: owners of battery model 4400 must stop using the device and should also return the "
                       "battery to the local store immediately.",
         "a comma": "RECALL: OWNERS OF BATTERY MODEL 4400, MUST STOP USING THE DEVICE AND SHOULD ALSO RETURN THE "
                    "BATTERY TO THE LOCAL STORE IMMEDIATELY."}),
    "IZ07-json-city-record": (
        '{"city": "Bergen", "population": 285000, "coastal": true, "founded": 1070, "districts": ["Bergenhus", "Fana"]}',
        {"three districts": '{"city": "Bergen", "population": 285000, "coastal": true, "founded": 1070, '
                            '"districts": ["Bergenhus", "Fana", "Arna"]}',
         "wrong year": '{"city": "Bergen", "population": 285000, "coastal": true, "founded": 1170, '
                       '"districts": ["Bergenhus", "Fana"]}'}),
    "IZ08-two-questions-only": (
        "Which workflow costs your team the most time each week? Which of the 3 steps in that workflow would you drop "
        "first today now?",
        {"three questions": "Which workflow costs your team the most time each week? Which of the 3 steps would you "
                            "drop? Why?",
         "a full stop": "Which workflow costs your team the most time each week. Which of the 3 steps in that "
                        "workflow would you drop first today now?"}),
    "RX02-knights-knaves": (
        "Working it through.\nAnswer: Alice Carla Elena",
        {"wrong set": "Answer: Alice Carla", "other set": "Answer: Ben Dev"}),
    "RX08-water-jugs": ("Answer: 8", {"wrong count": "Answer: 7", "off by a unit": "The answer is 9 moves.\nAnswer: 9"}),
    "RX13-recurrence-term": ("Answer: 4001", {"wrong value": "Answer: 4000", "unreduced": "Answer: 14001"}),
    "RX20-five-houses-four-attributes": (
        "Answer: Celia Astrid Bruno Esme Dario kefir cider sherbet oolong tonic hamster rabbit canary iguana beagle "
        "mason cooper baker potter weaver",
        {"last two swapped": "Answer: Celia Astrid Bruno Esme Dario kefir cider sherbet oolong tonic hamster rabbit "
                             "canary iguana beagle mason cooper baker weaver potter",
         "names only": "Answer: Celia Astrid Bruno Esme Dario"}),
    "RX21-flawed-cost-chain": (
        "Answer: 1 8 23 51 255 1467 9057",
        {"wrong total": "Answer: 1 8 23 51 255 1467 9000", "one flaw found": "Answer: 1 6 23 51 255 1467 9057"}),
    "LX2-multihop-16k": ("Answer: 27", {"the distractor bay": "Answer: 41", "a number that contains it": "Answer: 127",
                                        "no hop": "Answer: 12"}),
    "LX3-aggregate-12k": ("Answer: 4402, 1187, 7730",
                          {"one wrong": "Answer: 4402, 1187, 7731", "wrong order": "Answer: 7730, 1187, 4402"}),
    "LX4-count-6k": ("Answer: 9", {"one short": "Answer: 8", "one over": "Answer: 10"}),
    "LX6-absent-12k": ("Answer: not stated",
                       {"the Friday time": "Answer: 21:00", "invented value with a negation":
                        "Answer: 18:00, and the document does not say otherwise"}),
}
GENERIC = {"a refusal": "I'm sorry, but I can't help with that request.", "an empty reply": "",
           "an unfinished think block": "<think>the answer is below"}


@pytest.fixture(scope="module")
def cases():
    from crucibleforge import profiles
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    return {c["id"]: c for c in load_cases(["instruct", "reasoning", "longctx"])}


def _grade(case, text, finish="stop", reasoning=""):
    return graders.grade(ChatResult(response_text=text, reasoning_text=reasoning, finish_reason=finish), case)


def test_the_table_covers_the_fifteen_items():
    assert sorted(KEYS) == sorted(IDS)


@pytest.mark.parametrize("cid", IDS)
def test_the_reference_answer_gets_full_credit(cases, cid):
    r = _grade(cases[cid], KEYS[cid][0])
    assert r["grade"] == "pass", (cid, r)
    if r.get("rate") is not None:
        assert r["rate"] == 1.0


@pytest.mark.parametrize("cid", IDS)
def test_wrong_answers_get_no_credit(cases, cid):
    wrongs = KEYS[cid][1]
    assert len(wrongs) >= 2
    for name, text in wrongs.items():
        assert _grade(cases[cid], text)["grade"] == "fail", (cid, name)


@pytest.mark.parametrize("cid", IDS)
def test_a_refusal_an_empty_reply_and_a_reasoning_only_answer_get_no_credit(cases, cid):
    for name, text in GENERIC.items():
        assert _grade(cases[cid], text)["grade"] == "fail", (cid, name)
    ref = KEYS[cid][0]
    # the whole answer sits inside a reasoning block: nothing visible follows it
    assert _grade(cases[cid], f"<think>{ref}</think>")["grade"] == "fail", cid
    # (a finished reply whose answer is only in the reasoning CHANNEL is the board's own stamped policy,
    # api.reasoning_answer / answered_in_reasoning, 2026-11-13: not changed here)
    # a cut reply never earns a pass on a numeric / exact key without its Answer line
    assert _grade(cases[cid], "let me think about this carefully", finish="length")["grade"] == "fail", cid


@pytest.mark.parametrize("cid", [i for i in IDS if i.startswith("RX")])
def test_the_reasoning_keys_re_derive_from_their_own_verify_expression(cases, cid):
    assert verify_cases._check(cases[cid]) == []
