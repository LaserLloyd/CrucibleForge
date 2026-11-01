# watch time report

The family media server logs every play. Write ONE SQLite statement (CTEs
and window functions allowed) for the weekly "screen time" report. It
returns exactly the columns `name, local_date, sessions, total_minutes,
longest_minutes`, ordered by name, then local_date:

* A play covers the interval [started_at, started_at + seconds).
* A user's plays form SESSIONS: in start order, a play joins the current
  session if it starts no later than 30 minutes (1800 s) after the END of
  everything in that session so far (overlapping or touching plays always
  join); otherwise it starts a new session.
* A session belongs to the user's LOCAL date of its first play's start
  (UTC + `utc_offset_minutes`), formatted 'YYYY-MM-DD'.
* Per user and local date: `sessions` = number of sessions;
  `total_minutes` = the time covered by those sessions' plays, overlaps
  counted once, in minutes rounded to 1 decimal (`ROUND(x, 1)`);
  `longest_minutes` = the longest session (from its first start to its last
  end, gaps included), in minutes rounded to 1 decimal.
* Users with no plays do not appear.

On the sample:

| name | local_date | sessions | total_minutes | longest_minutes |
|---|---|---|---|---|
| Ana | 2026-03-01 | 1 | 40.0 | 60.0 |
| Ana | 2026-03-02 | 1 | 20.0 | 20.0 |
| Ben | 2026-03-01 | 1 | 91.0 | 121.0 |
