"""A full year: 150,000 receipts and bank lines, many amounts, card fees, refunds, pairs. The digest is of the old code's output on the same data
(computed once; the old code needs far longer than the test's time limit)."""
import _tr
from tillroll.reconcile import reconcile


def test_year_of_trade_is_fast_and_unchanged():
    bank, rec = _tr.spread(11, 150000)
    res = reconcile(bank, rec)
    assert _tr.digest(res) == "302f1796203aa1297662fceca6c5cb9dbddda9d4b77a251ce92bfe361cf2350e"
