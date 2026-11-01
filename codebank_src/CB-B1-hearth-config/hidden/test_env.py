import pytest

from _cfg import paths, write
from hearth.config import ConfigError, load_config


def test_env_beats_file_beats_default(tmp_path):
    p = write(tmp_path, 'version = 2\n[http]\nport = 9000\n[storage]\nkeep_days = 5\n')
    env = {"HEARTH_HTTP_PORT": "9100", "HEARTH_STORAGE_DIR": "/env/dir", "HEARTH_BACKUP_ENABLED": "YES",
           "HEARTH_BACKUP_TARGET": "usb", "HEARTH_BACKUP_SCHEDULE": "weekly", "HEARTH_HTTP_HOST": "::1",
           "UNRELATED": "x"}
    cfg, _ = load_config(p, env=env)
    assert cfg == {"http": {"host": "::1", "port": 9100}, "storage": {"dir": "/env/dir", "keep_days": 5},
                   "backup": {"enabled": True, "schedule": "weekly", "target": "usb"}}


@pytest.mark.parametrize("text,want", [("1", True), ("On", True), ("TRUE", True), ("off", False), ("No", False), ("0", False)])
def test_bools(tmp_path, text, want):
    env = {"HEARTH_BACKUP_ENABLED": text, "HEARTH_BACKUP_TARGET": "t"}
    cfg, _ = load_config(write(tmp_path, "version = 2\n"), env=env)
    assert cfg["backup"]["enabled"] is want


def test_port_alias(tmp_path):
    p = write(tmp_path, "version = 2\n")
    assert load_config(p, env={"HEARTH_PORT": "7000"})[0]["http"]["port"] == 7000
    assert load_config(p, env={"HEARTH_PORT": "7000", "HEARTH_HTTP_PORT": "7001"})[0]["http"]["port"] == 7001


def test_bad_env_values(tmp_path):
    p = write(tmp_path, "version = 2\n")
    with pytest.raises(ConfigError) as ei:
        load_config(p, env={"HEARTH_HTTP_PORT": "eighty", "HEARTH_BACKUP_ENABLED": "maybe",
                            "HEARTH_STORAGE_KEEP_DAYS": "1.5"})
    assert ei.value.errors == ["backup.enabled: bad value from HEARTH_BACKUP_ENABLED",
                               "http.port: bad value from HEARTH_HTTP_PORT",
                               "storage.keep_days: bad value from HEARTH_STORAGE_KEEP_DAYS"]
