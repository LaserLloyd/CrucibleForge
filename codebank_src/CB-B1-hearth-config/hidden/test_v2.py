from _cfg import write
from hearth.config import load_config

V2 = '''version = 2
[http]
host = "0.0.0.0"
port = 9443
[storage]
dir = "/srv/hearth"
keep_days = 7
[backup]
enabled = true
schedule = "weekly"
target = "nas:/backups"
'''


def test_full_v2(tmp_path):
    cfg, warnings = load_config(write(tmp_path, V2), env={})
    assert cfg == {"http": {"host": "0.0.0.0", "port": 9443},
                   "storage": {"dir": "/srv/hearth", "keep_days": 7},
                   "backup": {"enabled": True, "schedule": "weekly", "target": "nas:/backups"}}
    assert warnings == []


def test_v2_defaults(tmp_path):
    cfg, warnings = load_config(write(tmp_path, "version = 2\n"), env={})
    assert cfg == {"http": {"host": "127.0.0.1", "port": 8080},
                   "storage": {"dir": "/var/lib/hearth", "keep_days": 30},
                   "backup": {"enabled": False, "schedule": "daily", "target": None}}
    assert warnings == []


def test_v2_ignores_v1_names(tmp_path):
    cfg, warnings = load_config(write(tmp_path, 'version = 2\n[server]\nport = 1\n[storage]\npath = "/x"\n'), env={})
    assert cfg["http"]["port"] == 8080 and cfg["storage"]["dir"] == "/var/lib/hearth"
    assert warnings == ["unknown key: server.port", "unknown key: storage.path"]
