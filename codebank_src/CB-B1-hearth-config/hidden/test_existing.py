import pytest

from _cfg import write
from hearth.config import load_config


def test_v1_values(tmp_path):
    cfg, _ = load_config(write(tmp_path, '[server]\nport = 9000\n[storage]\nretention_days = 9\n'), env={})
    assert cfg["http"] == {"host": "127.0.0.1", "port": 9000} and cfg["storage"]["keep_days"] == 9


def test_release1_port_variable(tmp_path):
    cfg, _ = load_config(write(tmp_path, '[server]\nport = 9000\n'), env={"HEARTH_PORT": "9001"})
    assert cfg["http"]["port"] == 9001


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml", env={})


def test_bad_port_is_a_value_error(tmp_path):
    with pytest.raises(ValueError):
        load_config(write(tmp_path, '[server]\nport = 0\n'), env={})


def test_reads_os_environ_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HEARTH_PORT", "7777")
    cfg, _ = load_config(write(tmp_path, ""))
    assert cfg["http"]["port"] == 7777
