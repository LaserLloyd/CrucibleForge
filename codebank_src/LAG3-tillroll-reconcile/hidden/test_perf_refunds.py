"""A bad batch: 120,000 receipts of four prices, a third of them refunds. The digest is of the old code's output on the same data
(computed once; the old code needs far longer than the test's time limit)."""
import _tr
from tillroll.reconcile import reconcile


def test_many_refunds_are_fast_and_unchanged():
    bank, rec = _tr.refunds(14, 120000)
    res = reconcile(bank, rec)
    assert _tr.digest(res) == "dab89ea895158214f126afc6a10ded6b4a68cf8d1cf10d2c04173bf0108d5bd4"
