"""Opaque paging cursors."""
import base64
import binascii
import re

_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")


def encode_cursor(created_at: str, item_id: int) -> str:
    raw = f"v2|{created_at}|{item_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, int | None]:
    """(created_at, id); id is None for a release-1 cursor."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        text = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError):
        raise ValueError("bad cursor") from None
    if text.startswith("v2|"):
        parts = text.split("|")
        if len(parts) == 3 and _TS.fullmatch(parts[1]) and parts[2].isdigit():
            return parts[1], int(parts[2])
        raise ValueError("bad cursor")
    raise ValueError("bad cursor")
