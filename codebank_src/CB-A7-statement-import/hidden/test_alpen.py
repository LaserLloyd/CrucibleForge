from datetime import date

from stmtimport import Txn, import_csv

CSV = "Datum;Text;Soll;Haben\n02.03.26;REWE SAGT DANKE 4711;12,50;\n03.03.26;Gehalt Maerz;;2.345,67\n04.03.26;Stadtwerke Abschlag;1.234,56;\n"


def test_soll_and_haben_german_amounts():
    r = import_csv(CSV, "alpen", "sav")
    assert r.errors == []
    assert r.rows == [Txn(date(2026, 3, 2), -1250, "Rewe", "sav", "groceries"),
                      Txn(date(2026, 3, 3), 234567, "Gehalt Maerz", "sav", "income"),
                      Txn(date(2026, 3, 4), -123456, "Stadtwerke Abschlag", "sav", "utilities")]
