"""Loading hearth's settings (release 1: v1 format only)."""
import os
import tomllib

DEFAULTS = {
    "http": {"host": "127.0.0.1", "port": 8080},
    "storage": {"dir": "/var/lib/hearth", "keep_days": 30},
}


def load_config(path, env=None):
    """Read the TOML file at `path`. Returns (cfg, warnings)."""
    env = os.environ if env is None else env
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    cfg = {section: dict(values) for section, values in DEFAULTS.items()}
    server = raw.get("server", {})
    storage = raw.get("storage", {})
    if "host" in server:
        cfg["http"]["host"] = server["host"]
    if "port" in server:
        cfg["http"]["port"] = server["port"]
    if "path" in storage:
        cfg["storage"]["dir"] = storage["path"]
    if "retention_days" in storage:
        cfg["storage"]["keep_days"] = storage["retention_days"]
    if "HEARTH_PORT" in env:
        cfg["http"]["port"] = int(env["HEARTH_PORT"])
    port = cfg["http"]["port"]
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"bad port {port!r}")
    return cfg, []
