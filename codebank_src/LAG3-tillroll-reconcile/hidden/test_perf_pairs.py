"""Two months of two-item sales booked as one bank line (24,000 receipts, 12,000 lines). The digest is of the old code's output on the same data
(computed once; the old code needs far longer than the test's time limit)."""
import _tr
from tillroll.reconcile import reconcile


def test_paired_sales_are_fast_and_unchanged():
    bank, rec = _tr.pairs(13, 60, 400, 12000)
    res = reconcile(bank, rec)
    assert _tr.digest(res) == "d14477d43b15b928d17f6d3abb2d3c27e5450cc961ba933f89bffacf238c6979"
