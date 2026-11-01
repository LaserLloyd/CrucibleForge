from datetime import date

from stmtimport import Txn, import_csv

CSV = ("Buchungstag;Wertstellung;Empfaenger;Verwendungszweck;Betrag;Waehrung\n"
       "01.03.2026;03.03.2026;EDEKA Markt 0815;Einkauf;-87,30;EUR\n"
       "02.03.2026;02.03.2026;Spotify AB;Abo;-10,99;EUR\n"
       "05.03.2026;05.03.2026;Hotel Lac;Reise;-120,00;CHF\n"
       "06.03.2026;06.03.2026;Arbeitgeber GmbH;Gehalt Maerz;3.210,05;EUR\n"
       "07.03.2026;07.03.2026;Bad Row;x;zwoelf;EUR\n")


def test_nordbank_rows():
    r = import_csv(CSV, "nordbank", "joint")
    assert r.rows == [Txn(date(2026, 3, 1), -8730, "Edeka Markt", "joint", "groceries"),
                      Txn(date(2026, 3, 2), -1099, "Spotify Ab", "joint", "subscriptions"),
                      Txn(date(2026, 3, 6), 321005, "Arbeitgeber", "joint", "uncategorised")]
    assert r.skipped == 1
    assert len(r.errors) == 1 and r.errors[0].startswith("6:")


def test_nordbank_documented():
    from stmtimport import BANK_FORMATS
    assert BANK_FORMATS["nordbank"]["delimiter"] == ";" and BANK_FORMATS["nordbank"]["header"] is True
