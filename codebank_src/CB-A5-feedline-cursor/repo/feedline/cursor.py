"""Opaque paging cursors."""
import base64


def encode_cursor(created_at: str) -> str:
    return base64.urlsafe_b64encode(created_at.encode()).decode()


def decode_cursor(cursor: str) -> str:
    return base64.urlsafe_b64decode(cursor.encode()).decode()
