"""The operator's maintenance marker is an optional file named by $CF_MAINTENANCE_MARKER (unset = no check)."""
from crucibleforge import cli


def test_unset_means_no_check(monkeypatch, tmp_path):
    monkeypatch.delenv("CF_MAINTENANCE_MARKER", raising=False)
    assert cli.maintenance_marker_present() is False


def test_set_and_present(monkeypatch, tmp_path):
    m = tmp_path / "marker"
    monkeypatch.setenv("CF_MAINTENANCE_MARKER", str(m))
    assert cli.maintenance_marker_present() is False
    m.write_text("x", encoding="utf-8")
    assert cli.maintenance_marker_present() is True


def test_no_private_path_in_the_cli():
    from pathlib import Path
    assert "~/.local/state/" not in Path(cli.__file__).read_text(encoding="utf-8")   # no host path is hard-wired
