"""Loading hearth's settings: v1 (release 1) and v2 formats."""
import os
import tomllib

DEFAULTS = {
    "http": {"host": "127.0.0.1", "port": 8080},
    "storage": {"dir": "/var/lib/hearth", "keep_days": 30},
    "backup": {"enabled": False, "schedule": "daily", "target": None},
}
DEPRECATED = "config v1 is deprecated; see docs/config-v2.md"
V1 = {("server", "host"): ("http", "host"), ("server", "port"): ("http", "port"),
      ("storage", "path"): ("storage", "dir"), ("storage", "retention_days"): ("storage", "keep_days")}
V2 = {(s, k): (s, k) for s in DEFAULTS for k in DEFAULTS[s]}
ENV = [("HEARTH_HTTP_HOST", "http", "host", str), ("HEARTH_HTTP_PORT", "http", "port", int),
       ("HEARTH_STORAGE_DIR", "storage", "dir", str), ("HEARTH_STORAGE_KEEP_DAYS", "storage", "keep_days", int),
       ("HEARTH_BACKUP_ENABLED", "backup", "enabled", bool), ("HEARTH_BACKUP_SCHEDULE", "backup", "schedule", str),
       ("HEARTH_BACKUP_TARGET", "backup", "target", str)]
_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


class ConfigError(ValueError):
    def __init__(self, errors):
        self.errors = sorted(errors, key=lambda e: (e.split(":", 1)[0], e))
        super().__init__("; ".join(self.errors))


def _convert(value, kind):
    if kind is int:
        return int(value)
    if kind is bool:
        v = value.strip().lower()
        if v in _TRUE:
            return True
        if v in _FALSE:
            return False
        raise ValueError(value)
    return value


def load_config(path, env=None):
    """Read the TOML file at `path`. Returns (cfg, warnings)."""
    env = os.environ if env is None else env
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError([f"toml: {e}"]) from None
    cfg = {s: dict(v) for s, v in DEFAULTS.items()}
    warnings, unknown, errors = [], [], []
    version = raw.pop("version", 1)
    if isinstance(version, bool) or version not in (1, 2):
        raise ConfigError([f"version: unsupported {version}"])
    mapping = V1 if version == 1 else V2
    if version == 1:
        warnings.append(DEPRECATED)
    for section, values in raw.items():
        if not isinstance(values, dict):
            unknown.append(section)
            continue
        for key, value in values.items():
            target = mapping.get((section, key))
            if target is None:
                unknown.append(f"{section}.{key}")
            else:
                cfg[target[0]][target[1]] = value
    env_values = dict(env)
    if "HEARTH_PORT" in env_values and "HEARTH_HTTP_PORT" not in env_values:
        env_values["HEARTH_HTTP_PORT"] = env_values["HEARTH_PORT"]
    for var, section, key, kind in ENV:
        if var in env_values:
            try:
                cfg[section][key] = _convert(env_values[var], kind)
            except ValueError:
                errors.append(f"{section}.{key}: bad value from {var}")
    warnings += [f"unknown key: {k}" for k in unknown]
    bad_env = {e.split(":", 1)[0] for e in errors}

    def check(dotted, ok, reason):
        if dotted not in bad_env and not ok:
            errors.append(f"{dotted}: {reason}")

    h, s, b = cfg["http"], cfg["storage"], cfg["backup"]
    check("http.host", isinstance(h["host"], str) and h["host"] != "", "must be a non-empty string")
    check("http.port", isinstance(h["port"], int) and not isinstance(h["port"], bool) and 1 <= h["port"] <= 65535,
          "must be an int in 1..65535")
    check("storage.dir", isinstance(s["dir"], str) and s["dir"].startswith("/"), "must be an absolute path")
    check("storage.keep_days", isinstance(s["keep_days"], int) and not isinstance(s["keep_days"], bool)
          and s["keep_days"] >= 1, "must be an int >= 1")
    check("backup.enabled", isinstance(b["enabled"], bool), "must be a bool")
    check("backup.schedule", b["schedule"] in ("daily", "weekly"), "must be daily or weekly")
    if b["enabled"] is True:
        check("backup.target", isinstance(b["target"], str) and b["target"] != "",
              "required when backup.enabled is true")
    elif b["target"] is not None:
        check("backup.target", isinstance(b["target"], str) and b["target"] != "", "must be a non-empty string")
    if errors:
        raise ConfigError(errors)
    return cfg, warnings
