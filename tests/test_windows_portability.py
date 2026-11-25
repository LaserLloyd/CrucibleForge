"""Static guards for the Windows leg of CI, provable on any OS.

Windows' default text encoding is cp1252, so a bare ``read_text()`` /
``write_text()`` / ``open(path, "w")`` silently corrupts (or fails on) every
non-ASCII byte there. The cases, transcripts and reports are full of them. This
reads the source instead of hoping someone notices a red Windows run.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCANNED = ("crucibleforge", "scripts", "tests")
_NON_FILE_OPEN_BASES = {"os", "webbrowser", "gzip", "tarfile", "zipfile"}


def _sources():
    for where in SCANNED:
        yield from sorted((ROOT / where).rglob("*.py"))


def _text_io_without_encoding(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        kwargs = {k.arg for k in node.keywords}
        if name in ("read_text", "write_text"):
            if "encoding" not in kwargs:
                yield node.lineno, name
        elif name == "open" and "encoding" not in kwargs:
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id in _NON_FILE_OPEN_BASES):
                continue
            mode = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            elif isinstance(func, ast.Attribute) and node.args \
                    and isinstance(node.args[0], ast.Constant):
                mode = node.args[0].value            # Path.open("w")
            for k in node.keywords:
                if k.arg == "mode" and isinstance(k.value, ast.Constant):
                    mode = k.value.value
            if mode is not None and "b" in str(mode):
                continue
            yield node.lineno, f"open({mode!r})"


def test_every_text_read_and_write_names_its_encoding():
    offenders = [f"{p.relative_to(ROOT)}:{line}: {what}"
                 for p in _sources() for line, what in _text_io_without_encoding(p)]
    assert not offenders, "text I/O without encoding= (cp1252 on Windows):\n" + "\n".join(offenders)


def test_scrub_check_survives_a_cp1252_console():
    """The privacy scanner (and the git hooks that call it) must give a verdict
    on a default Windows console. 2026-10-18 review: it scanned the tree and then
    died printing its own check-mark (UnicodeEncodeError, cp1252)."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "scrub_check.py"), "--selftest"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, cwd=str(ROOT))
    assert "UnicodeEncodeError" not in p.stdout + p.stderr, p.stderr[-800:]
    assert p.returncode == 0, p.stdout[-800:] + p.stderr[-800:]


def test_shell_scripts_are_kept_lf_by_gitattributes():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in attrs and "scripts/hooks/*" in attrs


# ---------------------------------------------------------------- file locks (CI 2026-11-25: fcntl.flock on Windows)
_LOCK_HELPER = ROOT / "crucibleforge" / "filelock.py"


def test_no_module_but_the_lock_helper_locks_a_file_itself():
    """Every file lock goes through crucibleforge/filelock.py (msvcrt on Windows). A direct fcntl.flock / lockf
    elsewhere is a lock that Windows does not take (the package has no fcntl there)."""
    offenders = []
    for where in ("crucibleforge", "scripts"):
        for p in sorted((ROOT / where).rglob("*.py")):
            if p == _LOCK_HELPER:
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in ("flock", "lockf") \
                        and isinstance(node.value, ast.Name):
                    offenders.append(f"{p.relative_to(ROOT)}:{node.lineno}: {node.value.id}.{node.attr}")
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom)
                                                            and node.module else [])
                    if "fcntl" in names:
                        offenders.append(f"{p.relative_to(ROOT)}:{node.lineno}: import fcntl")
    assert not offenders, "file locks outside crucibleforge/filelock.py:\n" + "\n".join(offenders)


class _FakeMsvcrt:
    """msvcrt.locking as Windows does it: a byte range from the fd's position, one owner per range, a held range
    refused with OSError; LK_UNLCK of a range one does not hold is an error too."""
    LK_UNLCK, LK_LOCK, LK_NBLCK, LK_RLCK, LK_NBRLCK = 0, 1, 2, 3, 4

    def __init__(self):
        self.held: dict = {}
        self.calls: list = []

    def locking(self, fd, mode, n):
        import os
        st = os.fstat(fd)
        key = (st.st_dev, st.st_ino, os.lseek(fd, 0, os.SEEK_CUR), n)
        self.calls.append((mode, key[2]))
        if mode == self.LK_UNLCK:
            if self.held.get(key) != fd:
                raise OSError(13, "not locked")
            del self.held[key]
            return
        if key in self.held and self.held[key] != fd:
            raise OSError(36 if mode == self.LK_NBLCK else 13, "Resource deadlock avoided")
        self.held[key] = fd


def test_the_lock_helper_takes_and_releases_a_real_lock_through_the_windows_branch(tmp_path, monkeypatch):
    import sys

    from crucibleforge import filelock
    fake = _FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(filelock, "_win", lambda: True)
    monkeypatch.setattr(filelock, "WIN_POLL_S", 0.0)
    path = tmp_path / ".write.lock"
    a = open(path, "a+", encoding="utf-8")
    b = open(path, "a+", encoding="utf-8")
    try:
        a.write("pid 1 since then: crucibleforge all\n")
        filelock.lock(a, blocking=False)
        assert fake.calls[-1] == (fake.LK_NBLCK, filelock.LOCK_OFFSET)          # the lock byte, not the note
        assert a.tell() == path.stat().st_size                                   # position put back
        with pytest.raises(BlockingIOError):
            filelock.lock(b, blocking=False)                                     # exclusive, non-blocking
        with pytest.raises(BlockingIOError):
            filelock.lock(b, exclusive=False, blocking=False)                    # no shared lock on Windows
        b.seek(0)
        assert b.read().startswith("pid 1 since")                               # the holder note stays readable
        filelock.unlock(a)
        filelock.lock(b, blocking=False)                                         # free after the release
        filelock.unlock(b)
        filelock.lock(a)                                                         # blocking: taken when free
        filelock.unlock(a)
        assert not fake.held
    finally:
        a.close()
        b.close()


def test_the_results_write_lock_and_the_ledger_lock_on_the_windows_branch(tmp_path, monkeypatch):
    import sys

    from crucibleforge import filelock, spend, writelock
    fake = _FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(filelock, "_win", lambda: True)
    monkeypatch.setattr(writelock, "ancestor_holds", lambda p: False)           # a second process, not a child
    f = writelock.acquire(tmp_path, "all --models x")
    assert f is not None and fake.held
    with pytest.raises(writelock.ResultsBusy):
        writelock.acquire(tmp_path, "run --models x")
    writelock.release(f)
    assert not fake.held
    with spend._Ledger(tmp_path / "ledger.jsonl") as led:
        assert fake.held and led.f
    assert not fake.held
