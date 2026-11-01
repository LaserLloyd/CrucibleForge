# pulsecount

Tiny per-day event counters for the household dashboard, stored in SQLite.

Data rules:

* Exactly one row per (day, name). `count` is the total of every bump.
* `first_seen` / `last_seen` are the earliest / latest `at` timestamp ever
  bumped for that row (ISO-8601 UTC strings; events can arrive late, out of
  order).
* `note` is set by hand on the dashboard and must survive bumps.
* The schema version lives in `PRAGMA user_version`. `migrate()` brings any
  older database to the latest version and is safe to run again (a no-op on
  an up-to-date database).
* When duplicate rows of one (day, name) are merged, the surviving row keeps
  the smallest id, the summed count, the earliest first_seen, the latest
  last_seen, and the note of the smallest-id row whose note is not NULL or
  empty (NULL if there is none).
