# Config v2 (spec)

`load_config(path, env=None) -> (cfg, warnings)`; `env` defaults to `os.environ`.
`cfg` is always fully populated:

    {"http":    {"host": str, "port": int},
     "storage": {"dir": str, "keep_days": int},
     "backup":  {"enabled": bool, "schedule": str, "target": str | None}}

Defaults: http.host "127.0.0.1", http.port 8080, storage.dir "/var/lib/hearth",
storage.keep_days 30, backup.enabled false, backup.schedule "daily",
backup.target None.

## Versions
The top-level key `version` picks the format: missing or 1 = v1, 2 = v2.
Any other value is an error `version: unsupported <value>`.

* v1 keys: `[server] host, port` and `[storage] path, retention_days`, which
  mean http.host, http.port, storage.dir and storage.keep_days. A v1 file adds
  the warning `config v1 is deprecated; see docs/config-v2.md` (once).
* v2 keys: `[http] host, port`, `[storage] dir, keep_days`,
  `[backup] enabled, schedule, target`.
* Any other key (in either version) adds the warning
  `unknown key: <dotted.path>` and is ignored: one warning per unknown leaf
  key, e.g. `unknown key: http.hots`; a whole unknown table `[extra]` with
  keys a and b gives `extra.a` and `extra.b`; an unknown top-level value
  `x = 1` gives `x`. The unknown-key warnings come after the deprecation
  warning, sorted.

## Environment overrides
Applied after the file and before validation; an environment value beats the
file, which beats the default:

    HEARTH_HTTP_HOST, HEARTH_HTTP_PORT, HEARTH_STORAGE_DIR, HEARTH_STORAGE_KEEP_DAYS,
    HEARTH_BACKUP_ENABLED, HEARTH_BACKUP_SCHEDULE, HEARTH_BACKUP_TARGET

Integers are parsed with `int()`. Booleans accept 1/true/yes/on and
0/false/no/off in any letter case. A value that does not convert is the error
`<dotted.path>: bad value from <VARIABLE>`. `HEARTH_PORT` (release 1's name)
still works as an alias of HEARTH_HTTP_PORT; HEARTH_HTTP_PORT wins if both are set.

## Validation
All problems are collected, then `ConfigError` is raised with `.errors`, a
list of `"<dotted.path>: <reason>"` strings sorted by path. `ConfigError` is a
ValueError. Rules:

* http.host: a non-empty string
* http.port: an int (not a bool) in 1..65535
* storage.dir: a string holding an absolute path
* storage.keep_days: an int (not a bool) >= 1
* backup.enabled: a bool
* backup.schedule: "daily" or "weekly"
* backup.target: a non-empty string, required when backup.enabled is true

A file that is not valid TOML is the error `toml: <parser message>`. A
missing file raises FileNotFoundError as before.
