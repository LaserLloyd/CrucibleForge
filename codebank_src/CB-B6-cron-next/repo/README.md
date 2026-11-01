# cronplan

The home server's job scheduler shows "next run" times on the dashboard.
`cronplan.cron.parse(expr)` must parse a cron expression and
`cronplan.cron.next_fire(expr, after, zone)` must compute the next run
(both to do, spec in `docs/cron.md`). `cronplan.jobs` lists jobs with
their next run; it calls `next_fire`.
