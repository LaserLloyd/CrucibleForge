"""The board's "Incomplete" tag (2026-11-09): bench_all.completeness (pure),
report.html_rows keys, the page (badge, legend, hide-incomplete filter)."""
from __future__ import annotations

import shutil

import pytest

from crucibleforge import bench_all, report
from crucibleforge.templates.board import render_html

try:                              # reuse the node harness of the layout tests
    from test_board_layout import COMPS, rows as layout_rows, run_board
except ImportError:                # pragma: no cover
    from .test_board_layout import COMPS, rows as layout_rows, run_board

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
CASES = ["C1", "C2", "C3"]


def _pass(*ids, **kw):
    return [{"case_id": i, "grade": "pass", **kw} for i in ids]


def _done(rows=None, **kw):
    return bench_all.completeness(rows if rows is not None else _pass(*CASES), CASES, **kw)


# ----------------------------------------------------------- completeness

def test_all_complete():
    c = _done()
    assert c["complete"] is True and c["reasons"] == []


def test_a_missing_case_is_incomplete():
    c = _done(_pass("C1", "C2"))
    assert not c["complete"] and c["cases"] == 1
    assert c["reasons"] == ["1 case not run"]


def test_a_missing_repeat_is_incomplete_and_a_retryable_error_does_not_count():
    rows = _pass("C1", "C2") + [{"case_id": "C3", "grade": "error", "error_kind": "transport"}]
    assert _done(rows)["cases"] == 1
    rows = _pass("C1", "C2", "C3")            # C1 asked for 3 repeats, has repeat 1 only
    assert _done(rows, repeats={"C1": 3})["cases"] == 1


def test_rows_waiting_for_the_judge_are_incomplete():
    rows = _pass(*CASES)
    rows[0].update(needs_judge=True)          # no verdict yet
    rows[1].update(needs_judge=True, judge={"judge_failed": True, "attempts": 1})   # retry left
    c = _done(rows)
    assert not c["complete"] and c["judge"] == 2
    assert "2 rows awaiting judge" in c["reasons"]


def test_a_judge_failure_with_attempts_used_up_is_final():
    rows = _pass(*CASES)
    rows[0].update(needs_judge=True, judge={"judge_failed": True, "attempts": 2})
    assert _done(rows)["complete"] is True


def test_a_due_ladder_pack_is_incomplete_and_named():
    c = _done(ladder={"names": ["work/code#1"], "jobs": 4})
    assert not c["complete"] and c["ladder"] == ["work/code#1"]
    assert c["reasons"] == ["ladder: work/code#1 due"]
    assert _done(ladder={"names": [], "jobs": 0})["complete"] is True
    assert _done(ladder=None)["complete"] is True


def test_escalation_owed_is_incomplete():
    c = _done(escalation_jobs=2)
    assert not c["complete"] and c["reasons"] == ["2 escalation jobs owed"]


def test_final_errors_still_count_as_complete():
    rows = _pass("C1") + [
        {"case_id": "C2", "grade": "error", "error_kind": "template"},           # permanent
        {"case_id": "C3", "grade": "error", "error_kind": "transport"}]           # retryable...
    assert _done(rows)["cases"] == 1                                              # ...so C3 is still owed
    c = _done(rows, attempts={"C3": bench_all.MAX_CASE_ATTEMPTS})                 # ...until attempts are used up
    assert c["complete"] is True


def test_reasons_combine_in_the_tooltip_order():
    rows = _pass("C1")
    rows[0].update(needs_judge=True)
    c = _done(rows, ladder={"names": ["work/code#1"], "jobs": 1})
    assert c["reasons"] == ["2 cases not run", "1 row awaiting judge", "ladder: work/code#1 due"]


# ------------------------------------------------------------ html rows

def test_html_rows_carry_the_tag_and_leave_every_score_alone(monkeypatch):
    card = {"chat": 1, "coding": 2, "total": 3, "weights": {}, "tok_per_s": None, "components": {}}
    stats = {"a": {"scorecard": dict(card)}, "b": {"scorecard": {**card, "chat": 4}}}
    monkeypatch.setattr(report, "superseded_map", lambda *a, **k: {})
    base = report.html_rows(["a", "b"], stats)
    comp = {"a": bench_all.completeness(_pass("C1"), CASES), "b": _done()}
    tagged = report.html_rows(["a", "b"], stats, completeness=comp)
    assert "incomplete" not in base[0]
    by = {r["label"]: r for r in tagged}
    assert by["a"]["incomplete"] is True and by["a"]["incomplete_why"] == "2 cases not run"
    assert by["b"]["incomplete"] is False and by["b"]["incomplete_why"] is None

    def strip(rs):
        return [{k: v for k, v in r.items() if k not in ("incomplete", "incomplete_why")} for r in rs]
    assert strip(tagged) == base                       # same scores, ranks, order


# ----------------------------------------------------------------- page

def _page():
    rs = layout_rows()
    rs[1].update(incomplete=True, incomplete_why="3 cases not run · ladder: work/code#1 due")
    rs[0]["incomplete"] = False
    return render_html(rs, "sub", components=COMPS, footer=["f"], legend=[report.INCOMPLETE_LEGEND])


def test_page_has_the_filter_and_legend_only_when_something_is_incomplete():
    html = _page()
    assert 'id="hideIncomplete"' in html and "Incomplete:" in html and "inc-tag" in html
    plain = render_html(layout_rows(), "sub", components=COMPS, footer=["f"])
    assert 'id="hideIncomplete"' not in plain


@needs_node
def test_badge_shows_with_tooltip_rank_dash_and_the_filter_hides_it():
    out = run_board(_page(), """
      out.table = el('tbody').innerHTML;
      out.order = order();
      el('hideIncomplete').fire('change', {target: {checked: true}});
      out.hidden = order();
      out.count = el('count').innerHTML;
    """)
    assert out["table"].count('class="inc-tag"') == 1
    assert 'title="Incomplete: 3 cases not run · ladder: work/code#1 due"' in out["table"]
    assert out["table"].count('class="rank-inc"') == 1       # the "—" in place of its rank
    assert out["order"] == "m0,m1,m2"                        # sorting unchanged
    assert out["hidden"] == "m0,m2"
    assert "1 incomplete (hidden)" in out["count"]
