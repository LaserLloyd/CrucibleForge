# potkeeper

The household money book: bank statements are imported, transactions get a
category, rent and subscriptions are generated from recurring rules, every
category has a monthly budget, and at the end of the year the shared
expenses are settled between the adults. Python stdlib only; the ledger is a
JSON file.

This README is the contract. Where the code and this file disagree, this
file is right.

Layout:

* `potkeeper/money.py`: parsing and formatting amounts.
* `potkeeper/dates.py`: date parsing and calendar helpers.
* `potkeeper/ledger.py`: `Transaction`, `Ledger`.
* `potkeeper/store.py`: loading/saving the ledger and the household file.
* `potkeeper/household.py`: members and their split weights.
* `potkeeper/rules.py`: category rules.
* `potkeeper/importer.py`: CSV statement import.
* `potkeeper/recurring.py`: recurring transactions.
* `potkeeper/split.py`: splitting an amount between members.
* `potkeeper/budget.py`: monthly budgets with rollover.
* `potkeeper/reports.py`: month report and the year-end report.
* `potkeeper/goals.py`: savings goals.
* `potkeeper/tags.py`: free-form tags on transactions.
* `potkeeper/export.py`: CSV and JSON export.
* `potkeeper/api.py`: read-only WSGI JSON API for the kitchen tablet.
* `potkeeper/charts.py`: text bar charts and sparklines for the terminal.
* `potkeeper/cli.py`: the `potkeeper` command (`import`, `categorise`, `month`,
  `year-end`, `budget`, `goals`, `export`, `chart`).

## Conventions

Money is integer cents everywhere (an `int`, never a float). Spending is
negative: a 4.20 coffee is `-420`; a refund or salary is positive. Dates are
`datetime.date` (bank statements carry no time of day). Members are listed in
the household file in a fixed order, the "household order"; every tie between
members is broken by household order (earlier member first).

## Ledger (`ledger.py`, `store.py`)

Every transaction has an integer `id`, assigned by `Ledger.add` (1, 2, 3, ...).
Ids are never reused: removing a transaction does not free its id, so the
next transaction still gets a new, higher id, also after saving and loading
(the ledger file stores `next_id`; an old file without it starts at the
highest id + 1, or 1 when empty). `Ledger.between(frm, to)` returns the
transactions dated `frm` to `to`, both inclusive, in ledger order.

## Amounts (`money.py`)

`parse_amount(text) -> int` accepts, after stripping surrounding whitespace:
an optional sign (`+` or `-`), ASCII digits `0`-`9` (no other digit characters:
a statement containing them is corrupt) with optional thousands separators
(`1,234,567`: the first group has 1-3 digits, every later group exactly 3),
and an optional decimal point followed by exactly one or two digits (`12.5`
is 1250 cents). Instead of a sign the whole amount may be in parentheses,
which means negative: `(12.00)` is -1200. The result is exact
(`parse_amount("0.29") == 29`). Anything else raises `ValueError`
(`""`, `"12."`, `"1.234"`, `"1,23.00"`, `"--5"`, `"(-5)"`, `"12 EUR"`).

`format_cents(cents) -> str` gives `-1,234.50` style: optional minus, thousands
separators, always two decimals.

## Payees

`rules.normalise_payee(text)`: casefold, every run of whitespace becomes one
space, leading/trailing whitespace removed. Payees are compared only in this
normalised form (import de-duplication and category rules).

## Category rules (`rules.py`)

A rules file has one rule per line, `pattern => category`; blank lines and
lines starting with `#` are ignored. A rule matches a payee when its
normalised pattern is a substring of the normalised payee. When several rules
match, the one with the LONGEST normalised pattern wins; among equally long
patterns the rule that comes first in the file wins. No match:
`"uncategorised"`. `Rules.categorise(ledger)` sets the category of every
transaction that has none (a category set by hand is never overwritten).

## Import (`importer.py`)

A statement is a CSV file with a header row: `date,amount,payee` and an
optional `memo` column (columns in any order). Dates are ISO (`2026-12-03`)
or `DD.MM.YYYY`. Header names are case-insensitive, and a UTF-8 byte order
mark in front of the header (spreadsheet exports start with one) is ignored.
A row's key is `(date, cents, normalised payee)`.

Importing the same statement twice must not double anything, but two real
identical purchases (two coffees on one day) must both be kept. So per key:
if the ledger already holds `k` transactions with that key and the file has
`n` rows with it, the FIRST `min(k, n)` of those rows (in file order) count as
already imported and are skipped; the remaining rows are added, in file
order, with their own memo. `import_csv(ledger, text)` returns
`ImportResult(added, skipped)` (counts) and assigns new ids in file order.

## Recurring transactions (`recurring.py`)

A rule has a `start` date, `every` (`"weekly"`, `"monthly"` or `"yearly"`), an
`interval` n >= 1 and an optional inclusive `until`. Occurrence k (k = 0, 1,
...) is:

* weekly: `start + 7*n*k` days;
* monthly: `n*k` months after `start`, on the day of month of `start`; when
  that month is shorter, the last day of that month. The clamp never carries
  over to later occurrences: a rule starting 31 January falls on 31 Jan,
  28 (or 29) Feb, 31 Mar, 30 Apr, 31 May, ...
* yearly: `n*k` years after `start`, same day and month; 29 February becomes
  28 February in common years and is 29 February again in leap years.

`occurrences(rule, frm, to)` returns the occurrence dates in `[frm, to]`
(inclusive), ascending. `materialise(ledger, rule, frm, to)` adds a
transaction for every occurrence that the ledger does not already hold with
the rule's key (date, cents, normalised payee) and returns how many it added.

## Budgets (`budget.py`)

A category budget has a monthly `amount` (cents, >= 0), `rollover` (default
true), `cap_months` (default 1) and a first month `since` (`YYYY-MM`). For
each month from `since` on:

* `available = amount + carry_in` (the first month's `carry_in` is 0);
* `spent = -(sum of the month's transactions in that category)` (a refund
  lowers `spent`; `spent` may be negative);
* `left = available - spent`;
* carry to the next month: without rollover always 0. With rollover: when
  `left >= 0` at most `amount * cap_months` (anything above the cap is
  dropped); when `left < 0` the whole overspend carries (`left`): overspending
  is never forgiven.

`month_status(ledger, budget, "YYYY-MM")` returns
`{"available", "spent", "left", "carry_out"}` for that month.

## Splitting (`split.py`)

`split_cents(total, weights) -> list[int]`: weights are non-negative ints with a
positive sum. For `total >= 0` every member first gets
`floor(total * w / W)` (W = sum of weights); the cents still missing go one
each to the members with the largest remainder `total * w mod W`, equal
remainders in household order. A member with weight 0 always gets 0. For a
negative total the split is exactly the negation of the split of `-total`
(`split_cents(-x, w) == [-s for s in split_cents(x, w)]`), so an expense and its
refund cancel member by member. The parts always add up to `total`.

## Reports (`reports.py`)

Ranges are whole calendar periods, both ends inclusive: a month report
covers its first to its last day, a year report 1 January to 31 December.

`month_report(ledger, "YYYY-MM")` returns `{"income", "spending", "by_category"}`:
`income` = sum of positive amounts, `spending` = minus the sum of negative
amounts, `by_category` = a list of `(category, spent)` where `spent` = minus the
sum of all of that category's amounts (refunds included), for every category
with at least one transaction that month, sorted by `spent` descending, then
category name ascending. Transactions without a category count as
`"uncategorised"`.

## Household (`household.py`)

The household file lists the members in household order with their split
weight (non-negative int) and optional weight `changes`:

```json
{"members": [{"name": "Ana", "weight": 2}, {"name": "Ben", "weight": 1}],
 "changes": [{"from": "2026-07-01", "weights": {"Ben": 2}}]}
```

A change takes effect on its `from` date (inclusive) and sets the weights of
the members it names; everyone else keeps their weight. Changes apply in
date order (same date: file order). `Household.weights_on(d)` returns the
weights in effect on date `d`, in household order. Constructing a household
raises `ValueError` for duplicate names, a negative weight, a change naming
an unknown member, or when the weights in effect at some point (initially or
after any change) are all 0.

## Year-end (`reports.year_end`)

Agreed with Ben's dad for the 2026 books. `year_end(ledger, household, year)`
looks at the shared transactions of the year (`shared=True`; their
`paid_by` must be a member name, else `ValueError`) and returns:

* `"members"`: for every member in household order
  `{"name", "paid", "share", "balance"}`.
  `paid` = minus the sum of the amounts the member paid (an expense counts
  positive, a refund negative). `share`: rounding happens once per year, not
  per transaction. First each member's EXACT share is summed as a fraction
  over the year: for every shared transaction, `-amount * w / W` with the
  weights in effect on the transaction's date (`w` the member's, `W` their
  sum). Then the shares are rounded with the largest-remainder method: every
  member gets `floor(exact)`, and the cents still missing to reach the total
  (`T = -(sum of the shared amounts)`) go one each to the members with the
  largest fractional part `exact - floor(exact)`, equal parts in household
  order. `balance = paid - share`; the balances add up to 0.
* `"settle"`: the transfers `(from, to, cents)` that bring every balance to
  0 with as few transfers as possible, chosen exactly like this. Members with
  balance 0 take no part. The others are split into groups whose balances
  each add up to 0, as MANY groups as possible. The groups are picked one
  after the other: take the earliest member (household order) not yet in a
  group; its group is, among all zero-sum sets of the remaining members that
  contain it and still let the members left over form the largest possible
  number of zero-sum groups, the one with the fewest members, and among
  equally small ones the one whose sorted list of household positions is
  smallest (compared like Python lists). Inside a group, repeatedly the
  member with the most negative balance pays the member with the most
  positive balance `min(debt, credit)` (ties in household order). The
  transfers come group by group, in the order the groups were picked.
  `reports.settle(names, balances)` does this for any balances that add up
  to 0.
* `"by_category"`: like the month report, over the whole year (all
  transactions, shared or not).

`potkeeper year-end YEAR --json` prints this dict as JSON (transfers as
`[from, to, cents]` lists).

## Savings goals (`goals.py`)

A goal has a `target` (cents), what is `saved` so far and a `due` month
(`YYYY-MM`). `monthly_needed(goal, month)` is what to put aside each month from
`month` through `due` (both inclusive) to reach the target: the remaining
amount divided by the number of months, rounded UP to the cent; 0 when the
target is reached; when `month` is after `due` the whole remainder.

## Tags (`tags.py`)

Tags are lowercase words without spaces (`tags.clean` lowercases and strips
them and drops empty ones, keeping first-seen order without duplicates).
`tagged(ledger, tag)` returns the transactions carrying the tag, in ledger
order; `tag_totals(ledger, frm, to)` returns `{tag: sum of amounts}` over the
inclusive range.

## Export (`export.py`)

`to_csv(transactions)`: header `id,date,amount,payee,category,memo`, ISO dates,
amounts as plain decimals without thousands separators (`-1234.50`), an
empty cell for no category, `\n` line endings. `to_json(transactions)`: a
list of objects with the same fields plus `shared`, `paid_by` and `tags`,
amounts as integer cents.

## API (`api.py`)

Read-only, GET only: `/month/YYYY-MM` (the month report), `/year-end/YYYY`
(the year-end report), `/goals` (each goal with `monthly_needed` for the
current month). Unknown paths answer 404, other methods 405, bad parameters
400; every body is JSON.
