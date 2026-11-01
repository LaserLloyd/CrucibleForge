"""Turning a request path into a file under the drop root."""
from pathlib import Path
from urllib.parse import unquote


class UnsafePath(ValueError):
    """The request path points outside the drop root (or is otherwise not allowed)."""


def safe_path(root: Path, user_path: str) -> Path:
    """The file under `root` that `user_path` names. Raises UnsafePath."""
    if not user_path:
        raise UnsafePath("unsafe path")
    decoded = unquote(unquote(user_path))
    if "\x00" in decoded:
        raise UnsafePath("unsafe path")
    parts: list[str] = []
    for seg in decoded.replace("\\", "/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                raise UnsafePath("unsafe path")
            parts.pop()
            continue
        parts.append(seg)
    if not parts:
        raise UnsafePath("unsafe path")
    root_real = Path(root).resolve()
    real = root_real.joinpath(*parts).resolve()
    if real == root_real or root_real not in real.parents:
        raise UnsafePath("unsafe path")
    return Path(root).joinpath(*parts)
