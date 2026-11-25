"""PLAN-451-LIMITS step 4c (lead ruling 2026-11-25): a SEARCH/REPLACE block closed by a bare ``>>>>>>>`` is read,
through a wrapper around the frozen ``codebank.parse_edits`` (edittol), never by editing codebank.py; nothing else
loosens and the row says it was tolerated."""
from __future__ import annotations

import pytest

from crucibleforge import codebank, edittol, runner
from crucibleforge.api import ChatResult

GOOD = "a.py\n```python\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n```\n"
BARE = GOOD.replace(">>>>>>> REPLACE", ">>>>>>>")


@pytest.fixture
def tol():
    edittol.install()
    yield
    edittol.uninstall()


def test_without_the_wrapper_a_bare_closer_ruins_the_edit():
    edittol.uninstall()
    # read as a whole-file write holding the markers: the file is ruined, a correct fix scores as wrong
    assert [e["kind"] for e in codebank.parse_edits(BARE)[0]] == ["file"]
    assert len(codebank.parse_edits(GOOD)[0]) == 1


def test_a_bare_closer_of_a_well_formed_block_is_read(tol):
    edits, _ = codebank.parse_edits(BARE)
    assert edits == codebank.parse_edits(GOOD)[0] and edits[0]["replace"] == "x = 2\n"
    assert edittol.count(BARE) == 1 and edittol.count(GOOD) == 0


@pytest.mark.parametrize("text", [
    BARE.replace("<<<<<<< SEARCH\n", ""),                 # no opener
    BARE.replace("=======\n", ""),                        # no separator
    "a.py\n```python\nx = 1\n>>>>>>>\n```\n",              # no block at all
    GOOD + "\n>>>>>>>\n",                                 # after a closed block
])
def test_nothing_else_loosens(tol, text):
    assert edittol.count(text) == 0 and edittol.rewrite(text) == (text, 0)
    edittol.uninstall()
    plain = codebank.parse_edits(text)
    edittol.install()
    assert codebank.parse_edits(text) == plain


def test_a_non_matching_search_still_fails(tol):
    case_files = {"a.py": "y = 1\n"}
    edits, _ = codebank.parse_edits(BARE)
    tree, applied, errors = codebank.apply_edits(case_files, edits, ["a.py"], None)
    assert errors and tree["a.py"] == "y = 1\n"


def test_the_row_says_it_was_tolerated(tol, monkeypatch):
    monkeypatch.setattr(runner, "grade", lambda view, case: {"grade": "pass", "detail": "ok", "score": 1.0,
                                                             "applied": ["a.py"], "format_errors": []})
    row = {}
    res = ChatResult(response_text=BARE, finish_reason="stop")
    runner.grade_single_row(row, res, {"id": "C1", "category": "coding", "prompt": "x"}, False, 1000)
    assert row["tolerated"] == {"bare_closer": 1, "rev": edittol.REV}
    row2 = {}
    runner.grade_single_row(row2, ChatResult(response_text=GOOD, finish_reason="stop"),
                            {"id": "C1", "category": "coding", "prompt": "x"}, False, 1000)
    assert "tolerated" not in row2
    prose = {}
    monkeypatch.setattr(runner, "grade", lambda view, case: {"grade": "pass", "detail": "ok"})
    runner.grade_single_row(prose, res, {"id": "I1", "category": "instruct", "prompt": "x"}, False, 1000)
    assert "tolerated" not in prose                       # not graded by the edit parser


def test_the_regrade_tool_knows_the_switch():
    from crucibleforge import regrade
    assert "bare_closer" in regrade.SWITCHES and "bare_closer" in regrade.RECHECK_SWITCHES
