"""The realignment marks index (``calibration/MARKS-INDEX-45.md``) as code: which calibration files are frozen.

A marks, lexicon, prompt or reader file is used only when its sha256 is a row of the index for its own path. A
file dropped next to the others without a row (an unindexed addendum) is refused, never loaded silently.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = PKG_ROOT / "calibration" / "MARKS-INDEX-45.md"
ROW = re.compile(r"^\| `(calibration/[^`]+)` \| `([0-9a-f]{64})` \|")


class NotIndexed(ValueError):
    """A calibration file whose sha256 is not the index row for its path."""


def rows(index: Path | None = None) -> list[tuple[str, str]]:
    """[(path relative to the repo, sha256)] in index order."""
    p = Path(index or INDEX_PATH)
    if not p.is_file():
        return []
    return [m.groups() for ln in p.read_text(encoding="utf-8").splitlines() if (m := ROW.match(ln))]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_indexed(path: Path, index: Path | None = None) -> bool:
    path = Path(path)
    rel = f"calibration/{path.name}"
    sha = sha256_of(path)
    return any(r == rel and s == sha for r, s in rows(index))


def require(path: Path, index: Path | None = None) -> str:
    """The file's sha256, or :class:`NotIndexed` when it is not an index row (path + sha) of MARKS-INDEX-45.md."""
    path = Path(path)
    sha = sha256_of(path)
    if not any(r == f"calibration/{path.name}" and s == sha for r, s in rows(index)):
        raise NotIndexed(f"{path.name} (sha256 {sha[:12]}...) is not a row of MARKS-INDEX-45.md: "
                         "an addendum counts only once it is indexed")
    return sha
