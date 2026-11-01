from _cfg import write
from hearth.config import load_config

DEP = "config v1 is deprecated; see docs/config-v2.md"


def test_v1_mapping_and_warning(tmp_path):
    text = '[server]\nhost = "h"\nport = 81\n[storage]\npath = "/d"\nretention_days = 3\n'
    cfg, warnings = load_config(write(tmp_path, text), env={})
    assert cfg == {"http": {"host": "h", "port": 81}, "storage": {"dir": "/d", "keep_days": 3},
                   "backup": {"enabled": False, "schedule": "daily", "target": None}}
    assert warnings == [DEP]


def test_explicit_version_1_and_unknown_keys(tmp_path):
    text = 'version = 1\nzeta = 1\n[server]\nhots = "x"\nport = 82\n[backup]\nenabled = true\n[alpha]\nk = 1\n'
    cfg, warnings = load_config(write(tmp_path, text), env={})
    assert cfg["http"]["port"] == 82 and cfg["backup"]["enabled"] is False
    assert warnings == [DEP, "unknown key: alpha.k", "unknown key: backup.enabled",
                        "unknown key: server.hots", "unknown key: zeta"]


def test_empty_file_is_v1(tmp_path):
    cfg, warnings = load_config(write(tmp_path, ""), env={})
    assert warnings == [DEP] and cfg["http"]["port"] == 8080
