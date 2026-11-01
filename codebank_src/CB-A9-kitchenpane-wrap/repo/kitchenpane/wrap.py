"""Line wrapping for the status pane (see docs/wrap.md)."""
import textwrap


def wrap(text: str, width: int) -> list[str]:
    """Wrap ``text`` into lines of at most ``width`` display columns."""
    lines: list[str] = []
    for para in text.split("\n"):
        lines.extend(textwrap.wrap(para, width) or [""])
    return lines
