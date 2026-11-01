"""Render notifications as pane lines."""
from .wrap import wrap

BULLET = "* "


def render(notes: list[str], width: int = 40) -> list[str]:
    """Each note becomes a bullet; continuation lines are indented under the text."""
    out: list[str] = []
    for note in notes:
        body = wrap(note, width - len(BULLET))
        out.append(BULLET + body[0])
        out.extend(" " * len(BULLET) + line for line in body[1:])
    return out
