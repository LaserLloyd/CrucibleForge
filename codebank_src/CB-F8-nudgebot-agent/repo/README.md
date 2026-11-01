# nudgebot

Household chore reminders for the family chat. A timer calls
`nudgebot.dispatch.tick(now, config, state)` every few minutes; the nudges it
returns are posted, and the state is saved with `nudgebot.state.save_state`.
After a restart the state is read back with `load_state`.

## Config (`household.json`)
```json
{"tz": "America/Chicago", "quiet": "22:00-07:00",
 "members": ["Ana", "Ben", "Cleo"],
 "chores": [{"id": "bins", "title": "Bins out", "every": "weekly",
             "at": "07:00", "start": "2026-03-02", "rotate": true}]}
```

Inputs are always valid: every key shown is present, `members` is never
empty, `at` is `HH:MM` (00:00-23:59), `every` matches the forms below, all
datetimes passed in are aware, `now` never goes backwards between ticks, and
chore definitions do not change (members may).

## Schedule (`nudgebot/schedule.py`)
- `every` is `"daily"`, `"weekly"` or `"<N>d"` (N >= 1). Occurrences are at the
  local wall-clock time `at` (household `tz`) on `start`, `start + N days`,
  `start + 2N days`, ... counted in calendar days (daily N = 1, weekly N = 7).
- If `at` does not exist on that day (clocks go forward), the occurrence is at
  the first minute that exists after the gap (02:30 on the change day in
  Chicago becomes 03:00 CDT). If `at` happens twice (clocks go back), the
  occurrence is the first one.
- `next_due(chore, tz, after)` returns the first occurrence strictly after
  `after` (an aware datetime), as an aware UTC datetime.
- `last_due(chore, tz, now)` returns the latest occurrence at or before `now`
  (aware UTC), or None if the chore has not started.

## Quiet hours (`nudgebot/quiet.py`)
- `quiet` is `"HH:MM-HH:MM"` in local time and may wrap past midnight.
  Start is inside, end is not. Equal start and end means no quiet hours.
- `send_time(due, quiet, tz)`: a nudge due inside quiet hours is sent when that
  quiet period ends (local end time on the right date, as aware UTC);
  otherwise at `due`. Inside/outside is decided on the local wall-clock time of
  `due`. An end time that does not exist on that date, or happens twice, is
  resolved like a chore's `at` (first minute after the gap / first occurrence).

## Rotation (`nudgebot/rotation.py`)
- A rotating chore's occurrence goes to the member after the chore's last
  assignee in `members`, wrapping around. The very first one goes to
  `members[0]`.
- If the last assignee is no longer in `members`, it goes to the first member,
  among those listed after the last assignee in the roster saved with the last
  assignment (wrapping around that roster), who is still in `members`. If none
  is, `members[0]`. The roster is the `members` list as it was at the moment
  of that last assignment.
- A chore with `rotate: false` is for `"everyone"`.

## Dispatch (`nudgebot/dispatch.py`)
- `tick(now, config, state)` returns the nudges to post now, in config chore
  order, and updates `state` in place.
- For each chore only the LATEST occurrence at or before `now` matters; older
  missed ones are dropped (after downtime the family gets one nudge per chore,
  not a backlog). It is posted once: never again for the same occurrence, also
  after a restart.
- It is posted when its send time (quiet hours applied to its due time) is at
  or before `now`; until then it waits and later ticks post it. Quiet hours do
  not delay a nudge whose send time has already passed (after downtime it is
  posted at the next tick). `Nudge.send_at` is that send time, not the tick
  time.
- The assignee is chosen when the nudge is posted; only posted nudges advance
  the rotation.

## State (`nudgebot/state.py`)
- `save_state(path, state)` / `load_state(path)` round-trip the state exactly:
  `load_state` returns a value equal (`==`) to what was saved. A state holds
  dicts with string keys, lists, strings, ints, floats, bools, None and aware
  datetimes (which come back as aware datetimes; strings stay strings). A
  missing file loads as an empty state.
- The file is replaced atomically: if saving fails part-way, the error
  propagates and the previous file is left intact.
