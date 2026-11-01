from datetime import date

from stmtimport import Txn, import_csv, merge, normalize_payee


def test_harbor():
    r = import_csv("Date,Description,Amount\n03/02/2026,NETFLIX.COM 4433,-15.49\n", "harbor", "chk")
    assert r.rows == [Txn(date(2026, 3, 2), -1549, "Netflix.Com", "chk", "subscriptions")]


def test_payee():
    assert normalize_payee("REWE SAGT DANKE 4711*0815") == "Rewe"


def test_merge_skips_exact_duplicate():
    t = Txn(date(2026, 3, 2), -500, "Rewe", "chk", "groceries")
    ledger = [t]
    assert merge(ledger, [t]) == [] and ledger == [t]
