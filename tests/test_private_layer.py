"""The local-only layer (docs/CUSTOM-TESTS.md, "Keeping a custom test local-only").

- scripts/scrub_check.py derives every name the private layer defines (case
  file stems, case ids, overlay labels and custom keys) and refuses them in
  tracked files and commit messages, without echoing the matched text;
- scripts/private_layer.py backs the layer up and restores it byte for byte;
- a checkout without the layer (CRUCIBLEFORGE_NO_PRIVATE=1) lists its cases
  and builds the public board with no private trace and no error.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
CAT, IDS, LABEL, KEY = "zz-hush-cat", ["ZH1-quiet-one", "ZH2-quiet-two"], "Quiet Score", "quiet_score"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_layer(root: Path) -> None:
    (root / "cases" / "private").mkdir(parents=True, exist_ok=True)
    (root / "profiles").mkdir(exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "cases" / "tooluse.json").write_text(json.dumps([{"id": "TU1-public"}]), encoding="utf-8")
    (root / "cases" / "private" / f"{CAT}.json").write_text(
        json.dumps([{"id": i, "prompt": "p"} for i in IDS]), encoding="utf-8")
    (root / "cases" / "private" / "README.md").write_text("notes\n", encoding="utf-8")
    (root / "profiles" / "bench.private.yaml").write_text(yaml.safe_dump({
        "private": [CAT], "labels": {CAT: LABEL},
        "custom": {KEY: {"label": "Quiet Custom", "parts": {"main": {"max": 10, "scoring": [CAT]}}}}}),
        encoding="utf-8")


@pytest.fixture
def scrub(tmp_path, monkeypatch):
    mod = _load("scrub_check")
    _fake_layer(tmp_path)
    monkeypatch.setattr(mod, "REPO", tmp_path)
    if hasattr(mod.private_layer_terms, "_cache"):
        del mod.private_layer_terms._cache
    return mod


def test_scrub_derives_every_private_name(scrub):
    terms = scrub.private_layer_terms()
    assert {CAT, *IDS, LABEL, KEY, "Quiet Custom"} <= terms
    assert "TU1-public" not in terms and "tooluse" not in terms     # public names never banned


@pytest.mark.parametrize("line", [f"see the {LABEL} column", f"case {IDS[0]} failed",
                                  f"weights: {{{CAT}: 10}}", f"custom key {KEY}"])
def test_scrub_refuses_a_private_name_and_redacts_it(scrub, line):
    problems = scrub.scan_text(line, "msg")
    assert problems and "private test layer" in problems[0]
    joined = "\n".join(problems)
    for t in (CAT, LABEL, KEY, *IDS):
        assert t not in joined                                        # never echoed


def test_scrub_without_a_layer_derives_nothing(tmp_path, monkeypatch):
    mod = _load("scrub_check")
    monkeypatch.setattr(mod, "REPO", tmp_path)
    if hasattr(mod.private_layer_terms, "_cache"):
        del mod.private_layer_terms._cache
    assert mod.private_layer_terms() == set() and mod.private_layer_rules() == []


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_hook_refuses_a_commit_that_names_or_adds_the_layer(tmp_path):
    """End to end in a throwaway repo: the pre-commit hook blocks a forced-in
    private file and a tracked file that merely names a private label."""
    repo = tmp_path / "r"
    (repo / "scripts" / "hooks").mkdir(parents=True)
    for f in ("scrub_check.py", "private_layer.py", "install-hooks.sh"):
        shutil.copy(REPO / "scripts" / f, repo / "scripts" / f)
    for f in ("pre-commit", "commit-msg", "pre-push", "post-checkout", "post-merge"):
        shutil.copy(REPO / "scripts" / "hooks" / f, repo / "scripts" / "hooks" / f)
    shutil.copy(REPO / ".gitignore", repo / ".gitignore")
    _fake_layer(repo)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
           "HOME": str(tmp_path)}

    def git(*a, check=True):
        r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=env)
        if check:
            assert r.returncode == 0, r.stderr
        return r

    git("init", "-q")
    subprocess.run(["sh", "scripts/install-hooks.sh"], cwd=repo, env=env, capture_output=True, check=True)
    git("add", ".gitignore", "scripts", "cases/tooluse.json")
    git("commit", "-q", "-m", "base")
    # 1. a private file forced past .gitignore
    git("add", "-f", f"cases/private/{CAT}.json")
    r = git("commit", "-q", "-m", "oops", check=False)
    assert r.returncode != 0 and "cases/private/" in r.stderr + r.stdout
    git("reset", "-q")
    # 2. a public file that only mentions the private label
    (repo / "notes.md").write_text(f"The {LABEL} test is great.\n", encoding="utf-8")
    git("add", "notes.md")
    r = git("commit", "-q", "-m", "notes", check=False)
    out = r.stderr + r.stdout
    assert r.returncode != 0 and "private test layer" in out and LABEL not in out
    # 3. a generic one goes through
    (repo / "notes.md").write_text("A generic note.\n", encoding="utf-8")
    git("add", "notes.md")
    git("commit", "-q", "-m", "notes")


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes; Windows reports 0o777")
def test_backup_and_restore_round_trip(tmp_path, monkeypatch):
    pl = _load("private_layer")
    root, dest = tmp_path / "repo", tmp_path / "backups"
    root.mkdir()
    _fake_layer(root)
    monkeypatch.setattr(pl, "REPO", root)
    monkeypatch.setenv("CRUCIBLEFORGE_PRIVATE_BACKUP_DIR", str(dest))
    before = pl.manifest(root)
    assert f"cases/private/{CAT}.json" in before and "profiles/bench.private.yaml" in before
    assert "cases/tooluse.json" not in before                     # public files are not the layer
    a = pl.do_backup(quiet=True)
    assert a and oct(dest.stat().st_mode & 0o777) == "0o700" and oct(a.stat().st_mode & 0o777) == "0o600"
    assert pl.do_backup(if_changed=True, quiet=True) == a          # unchanged: no new archive
    with tarfile.open(a) as tf:
        assert pl.MANIFEST in tf.getnames()
    # simulate `git clean -fdx`
    shutil.rmtree(root / "cases" / "private")
    (root / "profiles" / "bench.private.yaml").unlink()
    assert pl.main(["restore", "latest"]) == 0 and not (root / "cases" / "private").exists()  # dry run
    assert pl.main(["restore", "latest", "--go"]) == 0
    assert pl.manifest(root) == before


def test_backup_keeps_only_the_newest(tmp_path, monkeypatch):
    pl = _load("private_layer")
    root = tmp_path / "repo"
    root.mkdir()
    _fake_layer(root)
    monkeypatch.setattr(pl, "REPO", root)
    monkeypatch.setenv("CRUCIBLEFORGE_PRIVATE_BACKUP_DIR", str(tmp_path / "b"))
    for i in range(4):
        pl.do_backup(label=f"n{i}", keep=2, quiet=True)
    assert len(pl.archives()) == 2


def test_empty_layer_is_a_clean_no_op(tmp_path, monkeypatch, capsys):
    pl = _load("private_layer")
    monkeypatch.setattr(pl, "REPO", tmp_path)
    monkeypatch.setenv("CRUCIBLEFORGE_PRIVATE_BACKUP_DIR", str(tmp_path / "b"))
    assert pl.main(["status"]) == 0 and pl.main(["backup"]) == 0 and pl.main(["check"]) == 0
    assert not (tmp_path / "b").exists()


def test_clean_checkout_lists_cases_without_any_private_trace():
    """`cases list` as a fresh clone sees it: works, and none of the real
    local layer's names appear (the layer, if any, is simply not there)."""
    env = {**os.environ, "CRUCIBLEFORGE_NO_PRIVATE": "1"}
    r = subprocess.run([sys.executable, "-c", "import sys; from crucibleforge.cli import main; sys.exit(main())",
                        "cases", "list"], cwd=REPO,
                       capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip()
    terms = _load("scrub_check").private_layer_terms()      # the REAL layer's names (empty in CI)
    leaked = [t for t in terms if t.lower() in (r.stdout + r.stderr).lower()]
    assert not leaked, f"{len(leaked)} private name(s) in a clean-clone listing"
