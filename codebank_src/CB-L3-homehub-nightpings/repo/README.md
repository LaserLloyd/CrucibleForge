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
