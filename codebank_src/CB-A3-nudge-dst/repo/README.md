# nudge

Recurring reminders ("water the plants, daily 07:00") for people in
different time zones. The scheduler stores and compares instants in UTC;
the rule itself is a wall-clock time in the person's zone.

Contract:

* `parse_rule(text)` reads `daily HH:MM <Zone>` or `weekly <day> HH:MM <Zone>`
  (`<day>` = mon tue wed thu fri sat sun, any letter case) into a dict
  `{"every": "day"|"week", "time": "HH:MM", "weekday": 0-6 (Monday = 0, weekly
  only), "tz": "<Zone>"}`. Anything else raises ValueError.
* `next_fire(rule, after)` returns the first fire instant strictly after the
  aware datetime `after`, as an aware UTC datetime. A reminder fires at most
  once per local calendar day.
* If the wall time does not exist that day (clocks jump forward over it) the
  reminder fires at the wall time shifted forward by the length of the jump
  (02:30 in a 02:00 to 03:00 jump fires at 03:30 local).
* If the wall time happens twice that day (clocks fall back) the reminder
  fires once, at the first occurrence.
* `occurrences(rule, start, end)` lists every fire instant in [start, end),
  in order: start included, end excluded.
* `to_iso(dt)` formats an instant as `YYYY-MM-DDTHH:MM:SSZ` (UTC).
