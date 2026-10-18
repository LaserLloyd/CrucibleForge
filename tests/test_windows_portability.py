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


def test_shell_scripts_are_kept_lf_by_gitattributes():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in attrs and "scripts/hooks/*" in attrs
