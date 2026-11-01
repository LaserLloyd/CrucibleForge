import io

import pytest

from nightwatch.config import ConfigError, load


def _cfg(tmp_path, verify):
    p = tmp_path / "nw.toml"
    p.write_text(f"[verify]\n{verify}\n[[target]]\nname = \"x\"\ndir = \"/x\"\npattern = \"*\"\n")
    return p


def _load(p):
    import sys
    err = io.StringIO()
    old = sys.stderr
    sys.stderr = err
    try:
        cfg = load(p)
    finally:
        sys.stderr = old
    return cfg, err.getvalue()


def test_old_key_still_works_with_warning(tmp_path):
    cfg, err = _load(_cfg(tmp_path, "max_age_h = 30"))
    assert cfg["max_age_hours"] == 30.0
    assert "max_age_h is deprecated; use max_age_hours" in err


def test_new_key_and_both(tmp_path):
    cfg, err = _load(_cfg(tmp_path, "max_age_hours = 12"))
    assert cfg["max_age_hours"] == 12.0 and "deprecated" not in err
    cfg, err = _load(_cfg(tmp_path, "max_age_hours = 12\nmax_age_h = 99"))
    assert cfg["max_age_hours"] == 12.0


def test_default_and_bad(tmp_path):
    cfg, _ = _load(_cfg(tmp_path, ""))
    assert cfg["max_age_hours"] == 26.0
    for bad in ("max_age_h = 0", "max_age_h = \"26\"", "max_age_hours = -1", "max_age_hours = true"):
        with pytest.raises(ConfigError):
            _load(_cfg(tmp_path, bad))


def test_shipped_config_loads():
    cfg, _ = _load("config/nightwatch.toml")
    assert cfg["max_age_hours"] == 26.0 and [t["name"] for t in cfg["targets"]] == ["photos", "home"]
