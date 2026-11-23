"""EL-17 wording list is advisory under the 4.5 standard (lead, 2026-11-14; the owner's rulings).

The only content limits are docs/CONTENT-RULES.md as written; rule 1 is judged on what is depicted,
not on words. Under ``element_standard: 45`` a wording hit (petite, student, dorm, ...) is a warning
naming the case and the term, waived per (case, term) by a reviewed reading in
docs/content-reviews.json; never an error, so nothing drops a card on a word. 4.1 cases (no key)
keep the error byte-identical. A stated age under 18 stays an error everywhere.
"""
from __future__ import annotations

import json

import pytest

from crucibleforge import element_lint as el

TEXT = "Thessaly Brannock, 30, a petite woman with warm brown eyes, meets Jonah (35) at the dorm bar."


def case(std=None, text=TEXT, cid="R16-r3-c4"):
    c = {"id": cid, "category": "nsfw", "rubric": "erp_session", "prompt": text,
         "characters": [{"name": "Thessaly Brannock", "age": 30}, {"name": "Jonah", "age": 35}]}
    if std is not None:
        c["element_standard"] = std
    return c


def test_4_1_keeps_the_error_byte_identical():
    errs, _ = el.lint_ages_split(case(), reviews={})
    assert errs == ["ambiguous age wording 'petite': state an adult age instead",
                    "ambiguous age wording 'dorm': state an adult age instead"]
    # a review never waives a 4.1 case
    rv = {("R16-r3-c4", "petite"): {"reading": "adult"}}
    assert el.lint_ages_split(case(), reviews=rv) == el.lint_ages_split(case(), reviews={})


def test_4_5_hit_is_a_warning_naming_case_and_term():
    errs, warns = el.lint_ages_split(case(45), reviews={})
    assert errs == []
    hits = [w for w in warns if "rule 1" in w]
    assert len(hits) == 2 and all("R16-r3-c4" in w for w in hits)
    assert any("'petite'" in w for w in hits) and any("'dorm'" in w for w in hits)
    assert el.lint_ages(case(45)) == []


def test_a_reviewed_reading_waives_exactly_that_case_and_term():
    rv = {("R16-r3-c4", "petite"): {"reading": "an adult body type"}}
    _, warns = el.lint_ages_split(case(45), reviews=rv)
    assert not any("'petite'" in w for w in warns) and any("'dorm'" in w for w in warns)
    _, other = el.lint_ages_split(case(45, cid="R99-x"), reviews=rv)
    assert any("'petite'" in w for w in other)


def test_an_under_18_age_stays_an_error_under_4_5():
    errs, _ = el.lint_ages_split(case(45, text="Mara (17), a petite student, and Jonah (35)."), reviews={})
    assert any("under 18" in e for e in errs) and not any("wording" in e for e in errs)


def test_lint_cases_counts_it_as_a_warning():
    errs, warns, lines = el.lint_cases([case(45)])
    assert errs == 0 and any(ln.startswith("warn R16-r3-c4/- EL-17: read R16-r3-c4") for ln in lines)


def test_the_reviews_file_shape(tmp_path):
    p = tmp_path / "content-reviews.json"
    p.write_text(json.dumps({"reviews": [{"case": "R16-r3-c4", "term": "Petite", "rule": 1,
                                          "reading": "adult body type", "by": "lead", "date": "2026-11-14"}]}), encoding="utf-8")
    rv = el.load_content_reviews(p)
    assert ("R16-r3-c4", "petite") in rv
    _, warns = el.lint_ages_split(case(45), reviews=rv)
    assert not any("'petite'" in w for w in warns)
    p.write_text(json.dumps({"reviews": [{"case": "R16-r3-c4", "term": "petite", "rule": 1}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="reading"):
        el.load_content_reviews(p)
    assert el.load_content_reviews(tmp_path / "absent.json") == {}
