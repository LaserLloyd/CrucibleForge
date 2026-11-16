"""adult45 N4 stored units (NR2-false-memory, NR4-four-hands) send their card history (scrub FIXNOW, 2026-11-15).

The two cases are the 4.x board's own (nsfw_rp set-1.json). Their history (10 / 12 messages) is the only place the
facts their checks read are set up: the sash was red (NR2), where each of the four hands rests (NR4). The adult page
built them with ``history=[]``, so every model was graded on facts it was never told. The history is sent as it
stands in set-1.json: role and content, in order, nothing re-worded."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crucibleforge import runner
from crucibleforge.gen import adult45

pytestmark = pytest.mark.live_ladder  # reads the live set-1.json (conftest guard)

SET1 = Path(__file__).resolve().parent.parent / "cases" / "ladder" / "nsfw" / "nsfw_rp" / "set-1.json"
SOURCE = {c["id"]: c for c in json.loads(SET1.read_text(encoding="utf-8"))["cases"]}


def _case(cid):
    return adult45.materialize({"type": "adult45", "variant": "stored", "level": 1, "pin": cid},
                               release_seed="t", case_id="t")


@pytest.mark.parametrize("cid,n", [("NR2-false-memory", 10), ("NR4-four-hands", 12)])
def test_stored_unit_sends_its_history(cid, n):
    src = SOURCE[cid]
    case = _case(cid)
    want = [{"role": m["role"], "content": m["content"]} for m in src["history"]]
    assert len(want) == n
    assert case["history"] == want
    assert case["turns"] == src["turns"] and case["system"] == src["system"]
    # what the runner sends on turn 1: system, the history in order, then the first user turn
    msgs = runner._messages_for(case, [{"role": "user", "content": case["turns"][0]}])
    assert msgs[0] == {"role": "system", "content": src["system"]}
    assert msgs[1:1 + n] == want
    assert msgs[-1] == {"role": "user", "content": src["turns"][0]}
    # the runner's rule for a card history: strictly alternating from the user, ending on the assistant
    assert [m["role"] for m in want] == ["user", "assistant"] * (n // 2)


def test_the_facts_the_checks_read_are_sent():
    """Without the history the checks cannot be met from what is sent: the turns never say the sash was red, nor
    that the upper right hand is at the shoulder blades."""
    nr2, nr4 = _case("NR2-false-memory"), _case("NR4-four-hands")
    assert not any("red" in t.lower().split() for t in nr2["turns"])
    assert any("red" in m["content"].lower() for m in nr2["history"])
    assert "shoulder" not in " ".join(nr4["turns"]).lower()
    assert any("shoulder" in m["content"].lower() for m in nr4["history"])


def test_history_is_a_copy():
    """Materialising never hands out the bank's own dicts (a caller that edits a case must not edit the bank)."""
    case = _case("NR2-false-memory")
    case["history"][0]["content"] = "x"
    assert _case("NR2-false-memory")["history"][0]["content"] != "x"
