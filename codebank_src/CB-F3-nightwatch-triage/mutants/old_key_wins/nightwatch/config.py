"""Reading config/nightwatch.toml."""
import sys
import tomllib

DEFAULT_MAX_AGE_HOURS = 26


class ConfigError(Exception):
    pass


def load(path, err=None):
    err = err or sys.stderr
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(str(e)) from None
    verify = raw.get("verify", {})
    if "max_age_h" in verify:
        print("max_age_h is deprecated; use max_age_hours", file=err)
        max_age = verify["max_age_h"]
    elif "max_age_hours" in verify:
        max_age = verify["max_age_hours"]
    else:
        max_age = DEFAULT_MAX_AGE_HOURS
    if not isinstance(max_age, (int, float)) or isinstance(max_age, bool) or max_age <= 0:
        raise ConfigError(f"max_age_hours must be a positive number, got {max_age!r}")
    targets = raw.get("target", [])
    for t in targets:
        for key in ("name", "dir", "pattern"):
            if key not in t:
                raise ConfigError(f"target is missing {key!r}")
    return {"max_age_hours": float(max_age), "targets": targets}
