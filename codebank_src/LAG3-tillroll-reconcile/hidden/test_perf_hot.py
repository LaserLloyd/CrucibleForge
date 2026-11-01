"""Coffee: 80,000 sales of two prices over four days, so every amount has thousands of receipts. The digest is of the old code's output on the same data
(computed once; the old code needs far longer than the test's time limit)."""
import _tr
from tillroll.reconcile import reconcile


def test_same_price_sales_are_fast_and_unchanged():
    bank, rec = _tr.hot(12, 80000)
    res = reconcile(bank, rec)
    assert _tr.digest(res) == "650144e2ed742ab1f1b9a8723a82072a8c605aa9e5368228a8cf101c5603925b"
