import pytest

from _cfg import paths, write
from hearth.config import ConfigError, load_config


def test_all_errors_sorted_by_path(tmp_path):
    text = '''version = 2
[http]
host = ""
port = 70000
[storage]
dir = "relative/dir"
keep_days = 0
[backup]
enabled = "yes"
schedule = "hourly"
'''
    with pytest.raises(ConfigError) as ei:
        load_config(write(tmp_path, text), env={})
    assert paths(ei.value) == ["backup.enabled", "backup.schedule", "http.host", "http.port",
                               "storage.dir", "storage.keep_days"]
    assert isinstance(ei.value, ValueError)


def test_target_required_only_when_enabled(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_config(write(tmp_path, "version = 2\n[backup]\nenabled = true\n"), env={})
    assert paths(ei.value) == ["backup.target"]
    cfg, _ = load_config(write(tmp_path, "version = 2\n[backup]\nenabled = false\n"), env={})
    assert cfg["backup"]["target"] is None


def test_bool_is_not_an_int(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_config(write(tmp_path, "version = 2\n[http]\nport = true\n[storage]\nkeep_days = true\n"), env={})
    assert paths(ei.value) == ["http.port", "storage.keep_days"]


@pytest.mark.parametrize("v", ["3", '"2"', "0"])
def test_unsupported_version(tmp_path, v):
    with pytest.raises(ConfigError) as ei:
        load_config(write(tmp_path, f"version = {v}\n"), env={})
    assert paths(ei.value) == ["version"] and "unsupported" in ei.value.errors[0]


def test_toml_syntax_error(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_config(write(tmp_path, "[http\nport = 1\n"), env={})
    assert len(ei.value.errors) == 1 and ei.value.errors[0].startswith("toml: ")
