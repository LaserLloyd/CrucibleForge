"""A fresh clone has no models.yaml — and CI is exactly that clone.

`cases list` / `cases verify` read nothing but the case files that ship with
the repo, so they must work with no registry at all. Requiring one made the
CI step that re-derives every gold answer exit 2 on every clean runner.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from crucibleforge import cli
from crucibleforge.config import ConfigError

REPO = Path(__file__).resolve().parent.parent


def _no_config(*a, **kw):
    raise ConfigError("no models.yaml found (searched ., <repo>, ~/.config)")


def test_cases_list_survives_a_missing_config(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", _no_config)
    with pytest.raises(SystemExit) as e:
        cli.main(["cases", "list"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "cases" in out
    assert "config error" not in out


def test_cases_verify_reaches_the_verifier_without_a_config(monkeypatch):
    """Don't run the whole (slow) verification here — just prove the config
    gate no longer stops the command before it starts."""
    monkeypatch.setattr(cli, "load_config", _no_config)
    seen = {}

    def fake_cases(args, cfg):
        seen["cfg"] = cfg
        seen["action"] = args.action
        return 0

    monkeypatch.setattr(cli, "cmd_cases", fake_cases)
    with pytest.raises(SystemExit) as e:
        cli.main(["cases", "verify"])
    assert e.value.code == 0
    assert seen == {"cfg": {}, "action": "verify"}


def test_other_commands_still_demand_a_config(monkeypatch, capsys):
    """Tolerating a missing registry is scoped to `cases`: a run without one
    must still fail loudly rather than start on an empty model list."""
    monkeypatch.setattr(cli, "load_config", _no_config)
    with pytest.raises(SystemExit) as e:
        cli.main(["status"])
    assert e.value.code == 2
    assert "config error" in capsys.readouterr().err


def test_config_init_without_the_template_is_a_clean_error(monkeypatch, tmp_path, capsys):
    """`models.example.yaml` only exists in a checkout. Missing it is a
    message and exit 2, never a FileNotFoundError traceback."""
    monkeypatch.setattr(cli, "EXAMPLE_CONFIG_PATH", tmp_path / "absent.yaml")
    with pytest.raises(SystemExit) as e:
        cli.main(["config", "--init", str(tmp_path / "models.yaml")])
    assert e.value.code == 2
    assert "missing" in capsys.readouterr().err
    assert not (tmp_path / "models.yaml").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_queue_script_requires_an_explicit_env_file(tmp_path):
    """No guessing a path under $HOME: the operator names the env file."""
    script = REPO / "scripts" / "queue-overnight.sh"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    p = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                       env=env, cwd=str(tmp_path))
    assert p.returncode == 2, p.stdout + p.stderr
    assert "CRUCIBLEFORGE_ENV_FILE" in p.stderr

    p = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                       env={**env, "CRUCIBLEFORGE_ENV_FILE": str(tmp_path / "nope.env")},
                       cwd=str(tmp_path))
    assert p.returncode == 2
    assert "does not exist" in p.stderr


def test_readme_says_the_supported_install_is_a_checkout():
    """Finding 2's minimal fix is documentation — if the note goes, the
    packaging gap is silent again."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "## Installing" in text
    assert "uv sync" in text
    assert "wheel" in text.lower() and "supported" in text.lower()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__]))
