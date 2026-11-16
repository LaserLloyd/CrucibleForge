"""The A7 bank (cases/ladder/nsfw/nsfw_writing/scene-bank.json): snippet levels, two sources per fact,
the two-source test on the drug-timing cells SCENE-SPEC's Core level draws, rule-4 lint, adults only,
attestation, and that the checks' vocabulary stays out of the prompts."""
import json
import re
from pathlib import Path
import pytest

from crucibleforge.gen import heat_schedule as hs
from crucibleforge.gen import scene_spec as ss

BANK = Path(__file__).resolve().parents[1] / "cases" / "ladder" / "nsfw" / "nsfw_writing" / "scene-bank.json"
B = json.loads(BANK.read_text(encoding="utf-8"))

#: the cells SCENE-SPEC's Core level (3) may draw; <= 15 by spec, single-site cells excluded
CORE_CELLS = {"sildenafil.onset", "sildenafil.peak", "cannabis_edible.peak", "cannabis_edible.duration"}


def host(u):
    return re.match(r"https?://([^/]+)", u).group(1).lower().removeprefix("www.")


def test_snippets_sit_at_their_level():
    for k, rows in B["heat"]["snippets"].items():
        assert len(rows) >= 8
        for s in rows:
            assert hs.level_of(s) == int(k), (k, s)
            assert 25 <= len(s.split()) <= 60
            assert not re.search(r"\b(?:he|she|him|her|his|hers)\b", s, re.I)
            assert "{A}" in s or "{B}" in s
            assert not re.sub(r"\{[AB]\}", "", s).count("{")
    c5 = B["heat"]["snippets"]["5"]
    assert sum(bool(re.search(hs.CLIMAX, s, re.I)) for s in c5) >= 3
    assert sum(not re.search(hs.CLIMAX, s, re.I) for s in c5) >= 3      # the no-climax mutant needs these


def test_level_definitions_and_titles_carry_no_marker():
    for v in list(B["heat"]["levels"].values()) + B["titles"] + B["settings"]:
        for k in (2, 3, 4, 5):
            assert not re.search(hs.MARKERS[k], v, re.I), (v, k)


def test_cast_is_adults_with_ages():
    assert len(B["cast"]) >= 10
    for c in B["cast"]:
        assert len(c["people"]) == 2
        for p in c["people"]:
            assert p["age"] >= 21 and p["name"].isalpha()


def test_bank_markers_match_the_modules():
    # the bank copies the climax regex for its known-error facts; it must equal the module's
    inj = B["scene"]["domains"]["edging"]["facts"][-1]
    assert inj["neg"] == hs.CLIMAX


def test_every_fact_has_two_distinct_sources_and_a_safe_kind():
    n = 0
    for dom in B["scene"]["domains"].values():
        for f in dom["facts"]:
            n += 1
            assert f["kind"] in ss.FACT_KINDS
            assert len(f["sources"]) >= 2, f["id"]
            assert len({s["url"] for s in f["sources"]}) >= 2 and all(s["quote"] for s in f["sources"]), f["id"]
            assert f.get("pos") or f.get("neg") or (f.get("pos_a") and f.get("pos_b"))
            assert f["role"] in ("any", "first", "last", "pre-peak")
            for rx in (f.get("pos"), f.get("neg"), f.get("pos_a"), f.get("pos_b")):
                if rx:
                    re.compile(rx)
    assert n >= 8


def test_two_source_test_on_the_core_clock_cells():
    cells = B["scene"]["clock"]["cells"]
    assert set(cells) == CORE_CELLS and len(cells) <= 15
    for k, c in cells.items():
        assert c["confidence"] == "high", k
        srcs = c["sources"]
        assert len(srcs) >= 2, k
        assert len({host(s["url"]) for s in srcs}) >= 2, f"{k}: two sources on different sites"
        assert all(s["quote"] and s["checked"] for s in srcs), k
        b = c["unit_band"]
        assert b["unit"] == "min" and 0 < b["min"] < b["max"]
    # the cells excluded for a missing second source are recorded, not silently dropped
    assert B["scene"]["clock"]["excluded_for_missing_second_source"]


def test_rule4_lint_clean_on_the_bank():
    probs = ss.rule4_lint(B["scene"], "scene.")
    assert probs == [], probs
    probs = ss.rule4_lint({"levels": B["heat"]["levels"]}, "heat.")
    assert probs == []


@pytest.mark.live_ladder
def test_scene_prompts_never_leak_the_hidden_facts():
    # the card states equipment properties and the zone; the accuracy facts stay hidden
    for lv in (1, 3, 5):
        for cid in ("P1", "P2", "P3", "P4"):
            m = ss.materialize({"type": "scene_spec", "level": lv}, release_seed="r", case_id=cid)
            p = m["turns"][0].lower()
            for w in ("kidney", "spine", "tailbone", "stearin", "refractory", "from a height"):
                assert w not in p, (w, cid)


def test_bank_is_attested_before_it_ships():
    att = B["meta"]["attest"]
    assert {a["by"] for a in att} >= {"deepseek"}, "DeepSeek Flash attestation missing"
    assert all(a["verdict"] == "PASS" and a["date"] for a in att)
    assert "grok" in B["meta"]["authorship"]["prose"]
