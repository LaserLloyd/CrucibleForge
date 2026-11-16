"""KNOWGAP generator (segment H6): determinism, reference 1.0 over many seeds, named floors, parser tolerance,
engine agreement, no accidental leaks of absent facts into the documents."""
import json
import re

import pytest

from crucibleforge import gen
from crucibleforge.gen import knowgap as kg

SEEDS = [f"K{i}" for i in range(40)]
LEVELS = sorted(kg.LEVELS)


def g(level, **kw):
    return {"type": "knowgap", "level": level, **kw}


def mat(level, cid="D1", rs="r"):
    return kg.materialize(g(level), release_seed=rs, case_id=cid)


def test_registered():
    assert "knowgap" in gen.families()
    assert gen.get("knowgap") is kg


@pytest.mark.parametrize("level", LEVELS)
def test_deterministic_and_seeds_differ(level):
    assert json.dumps(mat(level), sort_keys=True) == json.dumps(mat(level), sort_keys=True)
    assert len({mat(level, c)["prompt"] for c in SEEDS[:10]}) == 10
    assert mat(level, "S")["prompt"] != mat(level, "S", rs="r2")["prompt"]


@pytest.mark.parametrize("level", LEVELS)
def test_reference_is_one_and_shape(level):
    for cid in SEEDS:
        m = mat(level, cid)
        cfg = m["grader_config"]
        assert cfg["pairs"] == len(kg.LEVELS[level]["pairs"])
        assert len(cfg["items"]) == 2 * cfg["pairs"]
        ref = kg.reference(g(level), release_seed="r", case_id=cid)
        assert kg.grade(ref, m) == 1.0, cid
        # every pair has one value side and one decline side; the decline side has a lure
        for p in range(cfg["pairs"]):
            sides = sorted(it["side"] for it in cfg["items"] if it["pair"] == p)
            assert sides == ["decline", "value"]
        for it in cfg["items"]:
            if it["side"] == "decline":
                assert it["truth"] in (kg.NS, kg.CF) and it["lure"] is not None
            else:
                assert it["truth"] not in (kg.NS, kg.CF)
        # the two sides of a pair are never adjacent
        items = cfg["items"]
        assert all(items[i]["pair"] != items[i + 1]["pair"] for i in range(len(items) - 1))


@pytest.mark.parametrize("level", LEVELS)
def test_named_floors(level):
    for name in ("decline", "conflict", "copy", "answer_all", "empty"):
        scores = [kg.grade(kg.strategy(mat(level, c)["grader_config"], name), mat(level, c)) for c in SEEDS]
        assert max(scores) == 0.0, (name, level)


def test_lookup_only_reader_is_the_low_cell_only():
    lo = {lv: sum(kg.grade(kg.strategy(mat(lv, c)["grader_config"], "lookup_only"), mat(lv, c)) for c in SEEDS)
          / len(SEEDS) for lv in LEVELS}
    assert lo[1] == 1.0
    assert lo[3] <= 0.25 and lo[4] <= 0.30 and lo[5] <= 0.22 and lo[6] <= 0.10 and lo[7] == 0.0


@pytest.mark.parametrize("level", LEVELS)
def test_absent_values_never_appear_for_their_site(level):
    """An absent fact's lure must not be written as the asked site's own value (a leak would make NOT STATED
    contestable). We check the decline-side lure never appears in a sentence that names only the asked site."""
    for cid in SEEDS[:15]:
        m = mat(level, cid)
        assert "—" not in m["prompt"].split("Questions:")[1]
        for it in m["grader_config"]["items"]:
            if it["side"] == "value":
                continue
            assert it["truth"] in (kg.NS, kg.CF)


def test_parser_tolerance():
    cfg = mat(3, "P")["grader_config"]
    ref = kg.reference(g(3), release_seed="r", case_id="P")
    m = mat(3, "P")
    variants = [
        ref.replace("ANSWER:", "**ANSWER:**"),
        "\n".join("- " + ln for ln in ref.splitlines()),
        "\n".join("> " + ln for ln in ref.splitlines()),
        "Here are the answers.\n\n" + ref.replace("NOT STATED", "Not stated").replace("CONFLICT", "Conflict (the documents disagree)"),
        "\n".join(re.sub(r"ANSWER: ", "", ln) for ln in ref.splitlines()),        # bare "N. value"
    ]
    for v in variants:
        assert kg.grade(v, m) == 1.0, v[:80]


def test_value_forms():
    it = {"slot": "money", "truth": 522000}
    for f in ("522000", "522,000", "€522,000", "522000 EUR", "522 000"):
        assert kg.value_ok(f, it), f
    it = {"slot": "weight", "truth": 7400}
    for f in ("7400", "7,400 kg", "7.4 t", "7.4 tonnes"):
        assert kg.value_ok(f, it), f
    assert not kg.value_ok("7.4", it)
    it = {"slot": "date", "truth": "2027-06-25"}
    for f in ("2027-06-25", "25 June 2027", "June 25, 2027"):
        assert kg.value_ok(f, it), f
    it = {"slot": "person", "truth": "Ines Dunmore"}
    for f in ("Ines Dunmore", "Dunmore", "Ms Ines Dunmore"):
        assert kg.value_ok(f, it), f
    assert not kg.value_ok("Ines", it)


def test_classify():
    assert kg.classify("NOT STATED") == "NS"
    assert kg.classify("not given in the documents") == "NS"
    assert kg.classify("Cannot be determined") == "NS"
    assert kg.classify("CONFLICT") == "CF"
    assert kg.classify("The documents disagree") == "CF"
    assert kg.classify("14") == "V"


def test_engine_grader_agrees():
    from crucibleforge.api import ChatResult
    from crucibleforge.graders import grade
    m = mat(4, "E")
    ref = kg.reference(g(4), release_seed="r", case_id="E")
    out = grade(ChatResult(response_text=ref, finish_reason="stop"), m)
    assert out["score"] == 1.0 and len(out["results"]) == m["meta"]["elements"]
    bad = grade(ChatResult(response_text=kg.strategy(m["grader_config"], "decline"), finish_reason="stop"), m)
    assert bad["score"] == 0.0


def test_ceiling_per_level():
    for lv in LEVELS:
        assert mat(lv)["max_tokens"] == kg.CEILING[lv]
    assert kg.CEILING[1] >= 16384 and kg.CEILING[7] <= 65536


def test_mixed_line_shapes_and_day_first_dates():
    """Rig screen 2026-11-14: R1-8B mixed "N. ANSWER: x" and bare "N. x" lines in one reply, and wrote dates
    DDMMYYYY; both are read (grading only)."""
    m = mat(3, "MIX")
    items = m["grader_config"]["items"]
    lines = []
    for it in items:
        key = str(it["truth"])
        if it["slot"] == "date" and it["truth"] not in (kg.NS, kg.CF):
            y, mo, d = key.split("-")
            key = f"{d}{mo}{y}" if it["n"] % 2 else f"{d}/{mo}/{y}"
        lines.append(f"{it['n']}. ANSWER: {key}" if it["n"] % 2 else f"{it['n']}. {key}")
    assert kg.grade("\n".join(lines), m) == 1.0
    it = {"slot": "date", "truth": "2027-04-17"}
    assert kg.value_ok("17042027", it) and kg.value_ok("17-04-2027", it) and kg.value_ok("17.04.2027", it)
    assert not kg.value_ok("04172027", it)
