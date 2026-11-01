"""The monthly report for the full year. The digest is of the old code's output on the same data
(computed once; the old code needs far longer than the test's time limit)."""
import _tr
from tillroll.reconcile import reconcile
from tillroll.report import monthly_report


def test_year_report_is_fast_and_unchanged():
    bank, rec = _tr.spread(11, 150000)
    res = reconcile(bank, rec)
    assert _tr.text_digest(monthly_report(bank, rec, res)) == "919ae56671a244accd7f88db9cbcf10ff9623c3078d20b037d9245fd9588247e"
