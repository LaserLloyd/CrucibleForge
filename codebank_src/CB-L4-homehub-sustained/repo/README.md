# homehub

The household's home-automation hub: devices report readings, rules react to
events, a scheduler fires time-based triggers, and the API serves the
dashboard. Python stdlib only; state in SQLite.

Layout:

* `homehub/util/timeparse.py`: clock times, daily windows ("22:00-06:00"
  crosses midnight), durations ("90s", "15m", "2h").
* `homehub/util/units.py`: temperature and energy units.
* `homehub/events.py`: the event bus (`Event`, `Bus`), with de-duplication.
* `homehub/devices/`: device models (lights, plugs, thermostat, sensors).
* `homehub/rules/`: rule parsing (`conditions.py`), actions, the engine.
* `homehub/scheduler.py`: time-based triggers in the house's local zone.
* `homehub/energy.py`: energy accounting and cost with time-of-use tariffs.
* `homehub/notify.py`: notification throttling.
* `homehub/storage.py`: SQLite history.
* `homehub/api.py`: WSGI JSON API for the dashboard.
* `homehub/config.py`: loading `homehub.toml`.
* `homehub/hub.py`: wiring; `Hub.tick(since, until)` is called every minute by
  the service loop (scheduler triggers, then the notifier's quiet-hours summary).

Conventions: all instants are aware datetimes; anything stored or compared
is in UTC; local wall-clock times (windows, schedules, tariffs) are
interpreted in the house zone (`config.zone`, default "Europe/Berlin").
Money is integer cents; energy is integer watt-hours (Wh).

## Wanted in this release: sustained conditions

Rules need "for a while" conditions, e.g. "the front door has been open for
10 minutes":

    door.front == open for 10m
    temp.living < 18 and door.front == open for 10m

* A comparison may end with `for DURATION` (`90s`, `15m`, `2h`, `1d`, as in
  `util.timeparse.parse_duration`). It is true when the comparison has been
  true continuously for at least DURATION at the moment of evaluation. It
  becomes true-since at the first event or tick at which it is true, and the
  clock resets as soon as an evaluation finds it false.
* Every `for` comparison is tracked on every evaluation of its rule, whether
  or not the rest of the condition needs it at that moment (no short-cut
  evaluation for them).
* `time in WINDOW` cannot take `for` (ValueError), nor can anything else
  that is not a comparison; a malformed duration is a ValueError.
* Durations must also fire without new events: `Engine.check(now)` evaluates
  every rule that has a `for` comparison at time `now` (same rising-edge and
  cooldown rules as for events), and `Hub.tick(since, until)` calls
  `engine.check(until)` after publishing the scheduler's triggers and
  before the notifier's flush.
