# Cron expressions (spec)

Five space-separated fields: minute (0-59), hour (0-23), day of month
(1-31), month (1-12 or JAN-DEC), day of week (0-7 with 0 and 7 = Sunday, or
SUN-SAT). Names are case-insensitive.

Each field is a comma-separated list of items. An item is `*`, a number or
name, a range `a-b` (a <= b), or any of those with a step `/n` (n >= 1):
`*/15`, `10-50/20`, `5/10` (= from 5 to the field's maximum, step 10).

Extra items, only in the fields named:

* day of month: `L` (the month's last day), `nW` (the weekday Monday to
  Friday nearest to day n of the month, never leaving the month: day 1 on a
  Saturday gives Monday the 3rd, day 31 on a Sunday gives Friday the 29th; if
  day n does not exist in the month there is no run), `?` (same as `*`).
* day of week: `d#n` (the n-th weekday d of the month, n = 1..5, e.g. `5#2`
  = second Friday), `dL` (the last weekday d of the month), `?` (same as `*`).

Day matching: when both day of month and day of week are restricted (not
`*` or `?`), a day matches if EITHER matches. When only one is restricted,
only that one counts.

`parse(expr)` raises ValueError for anything else (wrong field count, out of
range values, a > b, step 0, `L`/`W`/`#` in the wrong field, `#` with n
outside 1..5).

`next_fire(expr, after, zone)`: `after` is an aware datetime, `zone` an IANA
zone name. Returns the first instant strictly after `after` whose LOCAL wall
time in `zone` matches the expression, as an aware UTC datetime (seconds 0).
Local times that do not exist (clocks jump forward) never match; a local
time that happens twice (clocks fall back) matches only its first
occurrence. If nothing matches within 5 years, raise ValueError.
