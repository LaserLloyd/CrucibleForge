# tillroll

Bookkeeping helpers for the shop (Mirela's bakery, two tills and the card
terminal). Python 3 standard library only.

* `tillroll/models.py`: `BankLine` (one line of the bank export) and
  `Receipt` (one sale or refund rung up on a till).
* `tillroll/money.py`: parsing and formatting amounts. Money is always
  integer cents.
* `tillroll/csvio.py`: reading the bank export and the till exports.
* `tillroll/vat.py`: the VAT summary for the quarterly return.
* `tillroll/zreport.py`: parsing the tills' end-of-day Z reports.
* `tillroll/reconcile.py`: matching bank lines to receipts.
* `tillroll/report.py`: the monthly reconciliation report for the accountant.
* `tillroll/cli.py`: `python -m tillroll.cli reconcile bank.csv receipts.csv`.

## Reconciliation rules

`reconcile(bank, receipts)` returns a `Result` with five lists. Bank ids are
unique. Receipt ids are not: each till numbers its own receipts, and after a
crash the till export can repeat a row (a repeated row is a receipt of its
own; Dana books both). Reports name a receipt by its ref, `"till:id"`.

Receipt order: by date, then id; receipts with the same date and id keep
their order in the export. Bank lines are taken in statement order: by date,
then id. "Open" means not matched or cancelled yet. A bank line or receipt
with amount 0 never matches anything.

0. **Refunds.** Every refund (amount < 0), in receipt order, cancels the
   latest open sale on the same till with the opposite amount that was rung
   up before it (an earlier date, or the same date and a lower id) and is at
   most 30 days older. "Latest" = last in receipt order.

Window: a receipt can be matched to a bank line when the receipt's date is
from 3 days before to 1 day after the bank line's date. Its distance is the
number of days between the two dates.

Card lines: a bank line whose memo starts with `"CARD "` is a card-terminal
payout. The terminal keeps a fee, so a card line matches a receipt when the
line's amount equals the receipt's amount minus `card_fee(receipt amount)`
(1.4 % rounded half up, at least 5 cents; refunds, i.e. amounts <= 0, carry
no fee). Every other line matches a receipt of exactly its amount.

1. **Same day.** Every bank line, in statement order, takes the first open
   receipt (in receipt order) on the same date with a matching amount.
2. **Window.** Then every still-open bank line, in statement order, takes
   the open matching receipt inside the window with the smallest distance;
   on equal distance the earlier receipt date; then the lowest id; then the
   first in receipt order.
3. **Pairs.** Then every still-open bank line that is not a card line, in
   statement order, takes two open receipts rung up on the same till on the
   same date (inside the window) whose amounts add up to the line's amount.
   If several pairs qualify: smallest distance, then earlier date, then the
   lowest of the two ids, then the lowest other id, then the pair whose
   first receipt comes first in receipt order, then whose second does.

`Result.cancelled` lists `(refund ref, sale ref)`, `Result.singles` lists
`(bank_id, ref)` in the order the matches were made (all of pass 1, then
pass 2), `Result.pairs` lists `(bank_id, ref, ref)` (the two receipts in
receipt order), and `Result.open_bank` (ids, statement order) /
`Result.open_receipts` (refs, receipt order; zero amounts and repeated rows
included) list what is left open.

## Monthly report

`monthly_report(bank, receipts, result)` gives one block per calendar month
that has a bank line or a receipt (oldest first): a summary line and up to
five "!" lines with the open bank lines of the largest absolute amount
(ties: earlier date, then lower id). See the docstring for the exact format.
Receipts with amount 0 are voided sales: counted as voided, never as open.

## Tests

`python -m pytest tests` (the tests use only plain asserts).
