# tabsplit

Shared-tab bookkeeping for a household: who paid what, who owes whom.

Rules (these are the contract; the tests and the report rely on them):

* Money is integer cents everywhere inside the package. Text amounts are
  parsed once, at the edge, by `tabsplit.money.parse_amount`.
* A split never loses or invents a cent: the shares of an expense always sum
  to the expense.
* Refunds are entered as negative amounts and are split exactly like the
  positive amount of the same size, with every share negated.
* `Ledger.balances()` always sums to zero.
