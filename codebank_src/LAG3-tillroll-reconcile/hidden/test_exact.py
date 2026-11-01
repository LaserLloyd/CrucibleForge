"""The speed-up must not change a single result: compare with the old code."""
import _tr
from _oracle import reconcile as old
from _oracle import report as old_report
from tillroll.reconcile import reconcile
from tillroll.report import monthly_report


def _same(bank, rec):
    got, want = reconcile(bank, rec), old.reconcile(bank, rec)
    assert list(map(tuple, got.cancelled)) == want.cancelled
    assert list(map(tuple, got.singles)) == want.singles
    assert list(map(tuple, got.pairs)) == want.pairs
    assert list(got.open_bank) == want.open_bank
    assert list(got.open_receipts) == want.open_receipts
    return want


def test_small_statements_match_old_code():
    for seed in range(1200):
        bank, rec = _tr.small(seed)
        _same(bank, rec)


def test_medium_statements_match_old_code():
    for bank, rec in (_tr.spread(5, 1500), _tr.hot(6, 1500), _tr.refunds(4, 1500), _tr.pairs(7, 4, 80, 300)):
        _same(bank, rec)


def test_reports_match_old_code():
    for seed in range(400):
        bank, rec = _tr.small(seed)
        res = old.reconcile(bank, rec)
        assert monthly_report(bank, rec, res) == old_report.monthly_report(bank, rec, res)
    bank, rec = _tr.spread(8, 1500)
    res = old.reconcile(bank, rec)
    assert monthly_report(bank, rec, res) == old_report.monthly_report(bank, rec, res)
