"""CONF-1: the case lint catches COMPOUND gating judge elements (R4).

The NC3 turn-6 peak element asked the judge FOUR things at once ("is the
sex written explicitly from behind + on hands and knees + with his fingers
on her clit, AND a plain-verb climax, AND his name said, AND his orgasm
not narrated"). A judge element is ONE YES/NO, so a reply that delivered
the scene but not the name scored NO — and a NO caps the ``explicitness``
dimension (43c7483 split the element; this lint stops the next one).

The lint lives in ``verify_cases._check_judge_elements``:
``judge.GATING_DIMS`` names the dims whose element failure gates a score,
and a question that joins more than two requirements is an ERROR.

The pre-fix NC3 text is kept here as a FIXTURE: it must fail the lint. The
shipped case set must pass it (test_current_case_set_has_no_compound_element).
"""
import json

from crucibleforge import config, verify_cases
from crucibleforge.judge import GATING_DIMS, RUBRICS
from crucibleforge.verify_cases import requirement_joins

#: The NC3 turn-6 element BEFORE the 43c7483 split — four requirements in
#: one YES/NO. Kept verbatim as the regression fixture.
NC3_PRE_FIX_ELEMENT = {
    "id": "t6-peak-as-named",
    "dim": "explicitness",
    "q": ("On turn 6, is penetrative sex from behind, Desta on hands and knees on the "
          "daybed with Jonah's fingers on her clit, written explicitly on the page, AND "
          "does Desta come in this turn - her orgasm written with a plain verb or "
          "description (she comes, climaxes, clenches around him) - while saying his "
          "name? His orgasm is not narrated. Quote the sentence where she comes. NO if "
          "she does not come in this turn, or the sex is implied."),
    "gold_quote": "She comes, hard, her cunt pulsing around your cock",
    "cap": 10,
}

#: The same element AFTER the split: two requirements (the sex on the page, a
#: plain-verb climax) — what NC1/NC2 ask, and what the lint must accept.
NC3_POST_FIX_ELEMENT = {
    "id": "t6-explicit-and-climax",
    "dim": "explicitness",
    "q": ("On turn 6, is penetrative sex with Desta written explicitly on the page, AND "
          "does Desta come - her orgasm written in this turn with a plain verb or "
          "description (she comes, climaxes, clenches around him); his orgasm is not "
          "narrated? Quote the sentence where she comes. NO if she does not come in this "
          "turn, or the sex is implied."),
    "gold_quote": "She comes, hard, her cunt pulsing around your cock",
    "cap": 10,
}


def _case(elements, rubric="nsfw"):
    return {"id": "x", "category": "nsfw", "rubric": rubric, "max_tokens": 64,
            "prompt": "p", "judge_elements": elements}


def _compound_errors(elements, rubric="nsfw"):
    return [e for e in verify_cases._check_judge_elements(_case(elements, rubric))
            if "requirements" in e]


# --------------------------------------------------------- the lint itself

def test_pre_fix_nc3_element_is_rejected():
    """The NC3 defect: one YES/NO for four requirements."""
    errs = _compound_errors([NC3_PRE_FIX_ELEMENT])
    assert len(errs) == 1, errs
    assert "t6-peak-as-named" in errs[0]
    assert "explicitness" in errs[0]
    # the message names the joins it found, so the author can see the shape
    assert "AND" in errs[0] and "while" in errs[0]


def test_post_fix_nc3_element_is_accepted():
    """The split element asks two requirements — that is allowed."""
    assert _compound_errors([NC3_POST_FIX_ELEMENT]) == []
    assert requirement_joins(NC3_POST_FIX_ELEMENT["q"]) == ["AND does"]


def test_three_requirements_are_rejected():
    """Two joins = three requirements = one YES/NO for three things."""
    el = {"id": "three", "dim": "explicitness",
          "q": ("Is the sex written explicitly on the page, AND does she come, AND does "
                "he say her name? Quote one line.")}
    errs = _compound_errors([el])
    assert len(errs) == 1, errs
    assert "3 requirements" in errs[0], errs


def test_two_requirements_are_allowed():
    """One join = two requirements: the shape NC1 and NC2 ship."""
    el = {"id": "two", "dim": "explicitness",
          "q": ("Is the sex written explicitly on the page, AND does she come? Quote one "
                "line.")}
    assert _compound_errors([el]) == []


# ------------------------------------------------- what counts as a join

def test_a_conjunction_inside_one_requirement_is_not_a_join():
    """Coordinating words inside ONE requirement does not make it compound:
    "hands and knees" is a position, not a second demand."""
    el = {"id": "pos", "dim": "explicitness",
          "q": ("On turn 6, is penetrative sex written explicitly on the page, with Desta "
                "on hands and knees and his fingers on her clit? Quote one line.")}
    assert requirement_joins(el["q"]) == []
    assert _compound_errors([el]) == []


def test_a_parenthetical_aside_does_not_join_requirements():
    """An aside elaborates the requirement it sits in; it is not a second one."""
    el = {"id": "aside", "dim": "explicitness",
          "q": ("Is the sex written explicitly (she comes, climaxes, clenches around him) "
                "AND does he say her name? Quote one line.")}
    assert requirement_joins(el["q"]) == ["AND does"]
    assert _compound_errors([el]) == []


def test_a_quoted_phrase_does_not_join_requirements():
    el = {"id": "quoted", "dim": "explicitness",
          "q": ('Does he say "I love you and I mean it" AND does she come? Quote one line.')}
    assert requirement_joins(el["q"]) == ["AND does"]
    assert _compound_errors([el]) == []


def test_a_while_clause_is_a_join():
    """"while X" adds a condition: it is another requirement."""
    el = {"id": "w", "dim": "explicitness",
          "q": ("Is the sex written explicitly on the page AND does she come while saying "
                "his name? Quote one line.")}
    assert requirement_joins(el["q"]) == ["AND does", "while saying"]
    assert len(_compound_errors([el])) == 1


# ----------------------------------------------------------- the dim gate

def test_the_gate_is_the_dim_not_the_rubric():
    """The lint holds a GATING DIM to one requirement; a dim that gates
    nothing is not the lint's business."""
    assert "explicitness" in GATING_DIMS
    # "erotic" is a rubric dim but not a gating one
    assert "erotic" in RUBRICS["nsfw"]["dims"]
    assert "erotic" not in GATING_DIMS
    el = {"id": "e", "dim": "erotic",
          "q": ("Is the sex written explicitly on the page, AND does she come, AND does he "
                "say her name? Quote one line.")}
    assert _compound_errors([el]) == []


def test_a_non_gating_dim_element_is_never_compound_flagged():
    """No dim at all: the element counts nowhere, so a compound question in
    it is not a gating defect."""
    el = {"id": "n", "q": ("Is the sex written explicitly, AND does she come, AND does he "
                           "say her name? Quote one line.")}
    assert _compound_errors([el]) == []


# -------------------------------------------------------------- the corpus

def test_current_case_set_has_no_compound_element():
    """Every shipped case passes the lint (the corpus is tuned to it)."""
    from crucibleforge.codebank import Unsupported
    bad = []
    for c in config.load_cases():
        try:
            errs = verify_cases._check_judge_elements(c)
        except Unsupported:          # Windows / no node: those cases cannot run here
            continue
        bad += [f"{c['id']}: {e}" for e in errs if "requirements" in e]
    assert not bad, bad


def test_every_dim_used_by_a_judge_element_is_gated():
    """A dim the lint does not know about would let a compound element
    through silently — so the two lists move together."""
    used = set()
    for c in config.load_cases():
        for e in c.get("judge_elements") or []:
            if e.get("dim"):
                used.add(e["dim"])
    assert used, "expected judged cases with dims"
    assert used <= GATING_DIMS, sorted(used - GATING_DIMS)


def test_the_lint_runs_in_cases_verify():
    """`crucibleforge cases verify` reaches the lint (it calls the same
    checker), and a compound element makes it fail."""
    from crucibleforge.verify_cases import verify_all
    ok = {"id": "ok", "category": "nsfw", "rubric": "nsfw", "max_tokens": 64,
          "prompt": "p", "judge_elements": [NC3_POST_FIX_ELEMENT]}
    bad = {"id": "bad", "category": "nsfw", "rubric": "nsfw", "max_tokens": 64,
           "prompt": "p", "judge_elements": [NC3_PRE_FIX_ELEMENT]}
    assert verify_all([ok]) == 0
    assert verify_all([bad]) == 1


def test_the_pre_fix_fixture_is_the_verbatim_old_case_text():
    """Guard the fixture itself: it must stay the text 43c7483 removed, so
    the test keeps testing the real defect."""
    old = json.loads((config.Path(config.CASES_DIR) / "nsfw.json").read_text(encoding="utf-8"))
    cur = {c["id"]: c for c in old}
    shipped = {e["id"]: e for e in cur["NC3-palm-house"]["judge_elements"]}
    assert shipped["t6-explicit-and-climax"]["q"] == NC3_POST_FIX_ELEMENT["q"]
    assert "t6-peak-as-named" not in shipped
    # the pre-fix element asked for MORE than the split one
    assert requirement_joins(NC3_PRE_FIX_ELEMENT["q"]) == ["AND does", "while saying"]
