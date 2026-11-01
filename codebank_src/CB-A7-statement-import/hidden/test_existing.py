from datetime import date

from stmtimport import (Txn, budget_alerts, export_qif, import_csv, monthly_totals, normalize_payee,
                        parse_amount_us, recurring)


def test_harbor_kiwi_errors():
    r = import_csv("Date,Description,Amount\n03/02/2026,NETFLIX.COM 4433,-15.49\n03/03/2026,Refund,(2.00)\nbad,row,x\n",
                   "harbor", "chk")
    assert [t.amount_cents for t in r.rows] == [-1549, -200] and r.errors[0].startswith("4:")
    k = import_csv("2026-03-04,Shell 22,40.10,\n2026-03-05,Refund Shell,,5.00\n", "kiwi", "card")
    assert [(t.amount_cents, t.category) for t in k.rows] == [(-4010, "car"), (500, "refunds")]


def test_reports():
    led = [Txn(date(2026, 3, d), -2000 * d, "Rewe", "chk", "groceries") for d in range(1, 9)]
    assert budget_alerts(led, "2026-03") == [("groceries", 72000, 60000)]
    assert monthly_totals(led)["2026-03"]["groceries"] == -72000
    q = export_qif([Txn(date(2026, 3, 2), -1549, "Netflix", "chk", "subscriptions")])
    assert q == "!Type:Bank\nD03/02'26\nT-15.49\nPNetflix\nLsubscriptions\n^\n"
    assert recurring([Txn(date(2026, m, 1), -999, "Netflix", "chk") for m in (1, 2, 3)]) == [("Netflix", -999, 3)]
    assert normalize_payee("  ALDI   SÜD  ") == "Aldi Süd" and parse_amount_us("1,234.56") == 123456
