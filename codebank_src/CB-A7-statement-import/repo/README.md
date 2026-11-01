# stmtimport

`stmtimport.py` (one file, grown over six years) turns the CSV exports of the
household's bank accounts into ledger rows `Txn(date, amount_cents, payee,
account, category)` and merges them into the ledger without duplicates.
`BANK_FORMATS` in the file documents each bank's CSV format.

Rules that must hold (the tests and the monthly report rely on them):

1. Amounts are integer cents; money in, positive; money out, negative.
2. Payees are normalised by `normalize_payee` (documented in the file).
3. `merge(ledger, new)` adds each new row unless the ledger already has a
   DUPLICATE of it: same account, same amount, same normalised payee, and
   dates at most `DUP_DAYS` (2) days apart (banks re-date pending card
   payments). Only rows that were in the ledger before this call count as
   possible duplicates, and each of them can absorb at most one new row (two
   identical coffees on the same day are two coffees). New rows are taken in
   order; a new row is absorbed by the first ledger row (in ledger order)
   that is a duplicate of it and has not absorbed one yet. It returns the
   rows it added, in order.
4. Categories come from `RULES` (first match wins), else "uncategorised".

## Wanted in this release

Add the format of the new joint account at **Nordbank**: key `"nordbank"`,
CSV with a header row and `;` separators, columns
`Buchungstag;Wertstellung;Empfaenger;Verwendungszweck;Betrag;Waehrung`:
date `DD.MM.YYYY` (use Buchungstag), payee = Empfaenger, amount like
`-1.234,56` (German: `.` thousands, `,` decimals), currency must be `EUR`
(any other currency: skip the row and count it in `ImportResult.skipped`).
