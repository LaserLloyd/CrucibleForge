"""Static guards for the Windows leg of CI, provable on any OS.

Windows' default text encoding is cp1252, so a bare ``read_text()`` /
``write_text()`` / ``open(path, "w")`` silently corrupts (or fails on) every
non-ASCII byte there. The cases, transcripts and reports are full of them. This
reads the source instead of hoping someone notices a red Windows run.
"""
from __future__ import annotations

import ast
from pathlib import Path

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
