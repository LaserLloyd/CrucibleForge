"""E4: the format gate is section-aware (WR2-style headed-section replies)."""
from crucibleforge import rpdeep

GOOD = ("## The Depot\n\nOdile Marsh pushes the cart along the dark bay, humming.\n\n"
        "## The Key\n\nShe finds a red thread tied to a brass key and says nothing.")
CASE = {"format": {"sections": ["The Depot", "The Key"]}, "format_gate": "chat"}


def test_default_gate_still_rejects_headings():
    assert rpdeep.ooc_reason(GOOD, "chat") == "markdown heading"


def test_declared_sections_pass():
    assert rpdeep.sections_declared(CASE)
    assert rpdeep.ooc_reason(GOOD, "chat", rpdeep.sections_declared(CASE)) is None


def test_check_naming_a_section_counts():
    assert rpdeep.sections_declared({"checks": [{"id": "x", "section": "The Key"}]})
    assert not rpdeep.sections_declared({"checks": [{"id": "x"}]})
    assert not rpdeep.sections_declared({})


def test_genuine_ooc_still_fails_with_sections():
    assert rpdeep.ooc_reason("((OOC: sure!)) ## Title\n\nShe nodded.", "chat", True)
    assert rpdeep.ooc_reason("Facts:\n- a\n- b\n- c", "chat", True)
    assert rpdeep.ooc_reason("Summary: the plot", "chat", True)


def test_ooc_flag_counts_threads_sections():
    g = {"turns": [1]}
    assert rpdeep.ooc_flag_counts(g, [GOOD], "chat") == "format gate on t1: markdown heading"
    assert rpdeep.ooc_flag_counts(g, [GOOD], "chat", True) is None


def test_score_row_does_not_zero_headed_reply():
    case = {"id": "T", "turns": ["go"], "format_gate": "chat", **CASE,
            "checks": [{"id": "C1", "fact": "C1", "ftype": "format", "group": "g",
                        "type": "require_regex", "turns": [1], "scope": "all",
                        "pattern": "Odile"}]}
    row = {"conversation": [{"role": "user", "content": "go"},
                            {"role": "assistant", "content": GOOD}]}
    out = rpdeep.score_row(row, case)
    assert "markdown heading" not in str(out)
