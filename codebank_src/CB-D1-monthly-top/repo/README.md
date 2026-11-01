# shop report: "top customer per month"

The household shop's owner wants one SQLite query for the first-half-year
report. The database follows `schema.sql`; `sample.sql` is a small sample.

Write ONE SQL statement (a SELECT, CTEs allowed) for SQLite that returns
exactly the columns `month, customer, net_cents`:

* One group of rows for every month from 2026-01 through 2026-06 (`month`
  as 'YYYY-MM'), even months without any activity.
* A customer's net spend in a month = the `total_cents` of their orders
  placed that month with status 'paid' or 'refunded' (a refunded order was
  paid first), minus the `amount_cents` of refunds made that month on any of
  their orders (a refund counts in the month of `refunded_at`, whatever
  month the order was placed). Pending orders never count.
* For each month, the customer(s) with the highest net spend, if that spend
  is above zero: one row per customer, so a tie gives several rows.
* A month where nobody has a net spend above zero gives one row with
  `customer` NULL and `net_cents` 0.
* Rows ordered by month, then customer name.

On the sample the answer is:

| month   | customer | net_cents |
|---------|----------|-----------|
| 2026-01 | Ben      | 7000      |
| 2026-02 | Ana      | 2500      |
| 2026-02 | Cy       | 2500      |
| 2026-03 | NULL     | 0         |
| 2026-04 | Cy       | 1200      |
| 2026-05 | NULL     | 0         |
| 2026-06 | NULL     | 0         |
