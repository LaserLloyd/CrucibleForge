"""E10: EL-17, every NSFW case states each character's adult age in its text."""
import json
from pathlib import Path

from crucibleforge import element_lint as el

ROOT = Path(__file__).resolve().parents[1]


def case(prompt, **kw):
    return {"id": "T", "rubric": "nsfw", "prompt": prompt, **kw}


def test_stated_adult_ages_pass():
    assert el.lint_ages(case("Two adults, Mara (32) and Jonah (35), meet.")) == []
    assert el.lint_ages(case("- ROZ ACHEBE (she/her), 35, climber.\n- SAM FERREIRA (he/him), 33, chef.")) == []
    assert el.lint_ages(case("INES CALLOWAY, 41, bassoonist; THEO MARSH, 44, tuner.")) == []
    assert el.lint_ages({"id": "T", "rubric": "erp_session",
                         "system": "Card: Rafe Collier is 41, a boat builder."}) == []


def test_missing_age_is_an_error():
    out = el.lint_ages(case("Two adults, Mara and Jonah, meet at a party."))
    assert any("no character age" in m for m in out)
    out = el.lint_ages(case("MARA (she/her), a baker, and JONAH, 35, meet."))
    assert any("MARA" in m and "no stated age" in m for m in out)


def test_minor_or_ambiguous_age_is_an_error():
    assert any("under 18" in m for m in el.lint_ages(case("Mara (17) and Jonah (35).")))
    assert any("ambiguous" in m for m in el.lint_ages(case("Mara (30) and a young girl, Jonah (35).")))
    assert any("ambiguous" in m for m in el.lint_ages(case("Mara (30), a high school student, and Jonah (35).")))
    assert el.lint_ages(case("Mara (30) and Jonah (35), a high-school music teacher.")) == []


def test_declared_characters_need_adult_age():
    c = case("Mara (32) and Jonah (35).", characters=[{"name": "Mara", "age": 32}, {"name": "Jonah"}])
    assert any("Jonah" in m for m in el.lint_ages(c))
    c = case("Mara (32).", characters=[{"name": "Mara", "age": 40}])
    assert any("not stated" in m for m in el.lint_ages(c))


def test_sfw_cases_are_not_checked():
    assert el.lint_ages({"id": "T", "rubric": "rp", "prompt": "A teen and a child play."}) == []


def test_lint_cases_reports_el17_as_error():
    errs, _, lines = el.lint_cases([case("Two adults meet.")])
    assert errs == 1 and "EL-17" in lines[0]


def test_public_nsfw_cases_pass():
    cases = json.loads((ROOT / "cases" / "nsfw.json").read_text(encoding="utf-8"))
    bad = {c["id"]: el.lint_ages(c) for c in cases if el.lint_ages(c)}
    assert bad == {}


def test_m4_each_named_character_needs_an_age():
    # the name heuristic is a WARNING (never an error), and needs a person cue
    c = case("Mara, 34, is a pilot. Late on Friday, Juno said she would stay the night.")
    errs, warns = el.lint_ages_split(c)
    assert any("Juno" in m for m in warns), warns
    assert not any("Juno" in m for m in errs) and not any("Mara" in m for m in warns)
    assert el.lint_ages(c) == []
    assert not any("named" in m for m in
                   el.lint_ages_split(case("Mara, 34, and Juno, 29, stay up late. Mara's flat is warm."))[1])
    # no person cue: places / languages / nouns do not warn
    w = el.lint_ages_split(case("Mara (34) flew to Lisbon and spoke Latin by the Fire."))[1]
    assert not any("Lisbon" in m or "Latin" in m or "Fire" in m for m in w), w
    errs, warns, lines = el.lint_cases([case("Mara, 34, and Juno said hi.")])
    assert errs == 0 and any("Juno" in x and x.startswith("warn") for x in lines)


def test_m4_student_dorm_wording_is_flagged():
    out = el.lint_ages(case("Mara (30) meets a young-looking student at the academy dorm."))
    assert sum("ambiguous" in m for m in out) >= 3, out
    assert any("petite" in m for m in el.lint_ages(case("Mara (30), petite and girlish, and Jonah (35).")))


def test_m4_public_nsfw_cases_stay_clean():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "cases"
    bad = []
    for f in sorted(root.glob("*.json")):
        if "private" in f.name:
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        for c in d if isinstance(d, list) else []:
            if isinstance(c, dict) and el.is_nsfw_case(c) and el.lint_ages(c):
                bad.append(c.get("id"))
    assert bad == []


def test_declared_characters_stated_near_name():
    ok = case("Mara, 34, and a 29-year-old red-haired Juno. Sam (41) waits.",
              characters=[{"name": "Mara", "age": 34}, {"name": "Juno", "age": 29},
                          {"name": "Sam", "age": 41}])
    assert el.lint_ages(ok) == []
    far = case("Mara is a pilot " + "x" * 80 + " she is 34.", characters=[{"name": "Mara", "age": 34}])
    assert any("not stated within 60" in m for m in el.lint_ages(far))
    assert any("no age" in m for m in el.lint_ages(case("Mara (34).", characters=[{"name": "Mara"}])))
    assert any("under 18" in m for m in el.lint_ages(case("Mara (34), Kit (17).",
                                                         characters=[{"name": "Kit", "age": 17}])))
    assert any("not stated" in m for m in el.lint_ages(case("Mara (34).",
                                                           characters=[{"name": "Ghost", "age": 30}])))


def test_ambiguous_youth_stays_an_error_via_lint_cases():
    errs, _, lines = el.lint_cases([case("Mara (30) and a student, Jonah (35).",
                                         characters=[{"name": "Mara", "age": 30},
                                                     {"name": "Jonah", "age": 35}])])
    assert errs >= 1 and any("ambiguous" in x and x.startswith("FAIL") for x in lines)


def test_standard_45_nsfw_requires_characters_older_only_warns():
    new = case("Mara (30) and Jonah (35).", element_standard=45)
    errs, _, lines = el.lint_cases([new])
    assert errs == 1 and any("must declare `characters`" in x for x in lines)
    old = case("Mara (30) and Jonah (35).")
    errs, warns, lines = el.lint_cases([old])
    assert errs == 0 and warns == 1 and lines[0].startswith("warn")
    done = case("Mara (30).", element_standard=45, characters=[{"name": "Mara", "age": 30}])
    assert el.lint_cases([done])[0] == 0
