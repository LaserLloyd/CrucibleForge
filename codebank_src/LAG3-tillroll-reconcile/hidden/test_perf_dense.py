"""The Christmas market weekend: 36,000 sales of the 40-item price list in
three days and 30,000 bank lines, most of which no two receipts add up to.
The digest is of the old code's output on the same data (computed once; the
old code needs far longer than the test's time limit)."""
import _tr
from tillroll.reconcile import reconcile


def test_market_weekend_is_fast_and_unchanged():
    bank, rec = _tr.dense(15, 3, 12000, 30000)
    res = reconcile(bank, rec)
    assert _tr.digest(res) == "4217ccdf979ed85d41a7770aeef520008f68b65c53530a8502dc6fc6d6df2e99"
