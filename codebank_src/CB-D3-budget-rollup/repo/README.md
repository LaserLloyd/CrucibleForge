# household budget: monthly roll-up

One SQLite statement (a SELECT, CTEs allowed) for the monthly budget page.
It takes one named parameter, `:month` ('YYYY-MM'), and returns exactly
the columns `path, own_cents, total_cents, budget_cents, remaining_cents,
over`, one row for EVERY category, ordered by `path`:

* `path`: the category's name prefixed by its ancestors' names, root first,
  joined with ' > ' (e.g. 'Home > Food > Groceries'). Categories form a tree
  of any depth.
* `own_cents`: the sum of the category's own spend rows dated in `:month`
  (refunds are negative and count); 0 if none.
* `total_cents`: own_cents of the category plus all its descendants.
* `budget_cents`: NULL if the category has no budget row for `:month`.
  Otherwise its budget for `:month` plus the carry-over: if the category also
  had a budget for the previous calendar month and its total spend in that
  month was below that budget, the unused amount is added (only one month
  back; an overspent month carries nothing).
* `remaining_cents`: budget_cents - total_cents, NULL when budget_cents is NULL.
* `over`: 1 when budget_cents is not NULL and total_cents > budget_cents,
  else 0.

On the sample with `:month = '2026-03'`:

| path | own | total | budget | remaining | over |
|---|---|---|---|---|---|
| Home | 2000 | 50500 | NULL | NULL | 0 |
| Home > Energy | 16000 | 16000 | 15000 | -1000 | 1 |
| Home > Food | 0 | 32500 | 46000 | 13500 | 0 |
| Home > Food > Groceries | 28000 | 28000 | 30000 | 2000 | 0 |
| Home > Food > Takeaway | 4500 | 4500 | NULL | NULL | 0 |
| Kids | 0 | 0 | NULL | NULL | 0 |
