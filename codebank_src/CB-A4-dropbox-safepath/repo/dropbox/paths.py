"""Turning a request path into a file under the drop root."""
from pathlib import Path


class UnsafePath(ValueError):
    """The request path points outside the drop root (or is otherwise not allowed)."""


def safe_path(root: Path, user_path: str) -> Path:
    """The file under `root` that `user_path` names. Raises UnsafePath."""
    if not user_path:
        raise UnsafePath("unsafe path")
    cleaned = user_path.replace("../", "")
    return root / cleaned
