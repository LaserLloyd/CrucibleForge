"""A7: compliance is not a capability. Every NR and NM case carries the refusal and disclaimer guards, which run
and report but earn nothing; the scored compliance items (NR1 red-means-stop, NR6 no-sex-while-high) are unscored;
NR5 is the PROTOCOL case, not safeword handling; the two NSFW packs state why they exceed the 5-minute limit."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "cases" / "ladder" / "nsfw"
RP = json.loads((ROOT / "nsfw_rp" / "set-1.json").read_text(encoding="utf-8"))
NM = json.loads((ROOT / "nsfw_writing" / "set-1.json").read_text(encoding="utf-8"))
CASES = RP["cases"] + NM["cases"]


@pytest.mark.parametrize("c", CASES, ids=lambda c: c["id"])
def test_guards_present_and_unscored(c):
    by = {k["type"]: k for k in c["checks"] if k["type"] in ("refusal_phrase", "disclaimer")}
    assert set(by) == {"refusal_phrase", "disclaimer"}, c["id"]
    for k in by.values():
        assert k["scored"] is False and k["weight"] == 0


def test_scored_compliance_items_are_unscored():
    nr1 = next(c for c in RP["cases"] if c["id"].startswith("NR1"))
    assert next(k for k in nr1["checks"] if k["id"] == "t3-red-means-stop")["scored"] is False
    nr6 = next(c for c in RP["cases"] if c["id"].startswith("NR6"))
    assert next(k for k in nr6["checks"] if k["id"] == "t2-no-sex-agreed-away")["scored"] is False
    assert next(e for e in nr6["judge_elements"] if e["id"] == "t2-no-sex-while-high")["scored"] is False
    # the ceiling items stay scored: counting, timing windows
    assert all(k.get("scored", True) for k in nr1["checks"] if k["id"] in ("t1-says-five", "t2-says-six", "t3-six-edges"))


def test_nr5_is_the_protocol_case():
    c = next(c for c in RP["cases"] if c["id"].startswith("NR5"))
    assert c["id"] == "NR5-protocol"
    text = json.dumps({k: c[k] for k in ("system", "turns", "checks", "judge_elements", "judge_key")}).lower()
    assert "safeword" not in text and "yellow" not in text and "traffic" not in text
    assert {k["id"] for k in c["checks"]} >= {"t1-order-present", "t2-order-hold", "t3-again-ritual", "no-plain-orders"}
    assert len(RP["cases"]) == 6


def test_packs_state_why_they_exceed_the_time_limit():
    for spec in (RP, NM):
        assert isinstance(spec.get("allow_long"), str) and len(spec["allow_long"].split()) >= 8
