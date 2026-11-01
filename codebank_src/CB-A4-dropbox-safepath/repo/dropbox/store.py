"""Reading and writing dropped files."""
from pathlib import Path

from .paths import safe_path


def save(root: Path, user_path: str, data: bytes) -> str:
    """Store `data`; returns the path relative to root (POSIX form)."""
    target = safe_path(root, user_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target.relative_to(root).as_posix()


def load(root: Path, user_path: str) -> bytes | None:
    target = safe_path(root, user_path)
    if not target.is_file():
        return None
    return target.read_bytes()
