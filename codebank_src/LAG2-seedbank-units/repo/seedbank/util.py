"""Small helpers shared by the other modules."""
import json
import re
import unicodedata
from datetime import datetime, timezone

_clock = None


def set_clock(fn):
    """Tests freeze time with set_clock(lambda: datetime(...)); None resets."""
    global _clock
    _clock = fn


def now():
    return _clock() if _clock else datetime.now(timezone.utc)


def now_iso():
    return now().astimezone(timezone.utc).replace(microsecond=0).isoformat()


def slug(text):
    """'Cherokee Purple!' -> 'cherokee-purple' (ASCII, for export file names)."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return t or "seed"


def fold(text):
    """Case- and accent-insensitive comparison key."""
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).casefold()


def read_json(environ, limit=64 * 1024):
    """Body of a WSGI request as a JSON object; ValueError when it is not one."""
    try:
        n = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        n = 0
    if n > limit:
        raise ValueError("body too large")
    raw = environ["wsgi.input"].read(n) if n else b""
    try:
        obj = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("body is not JSON") from None
    if not isinstance(obj, dict):
        raise ValueError("body must be a JSON object")
    return obj


def year_or_none(v):
    """'2023' / 2023 -> 2023; '' / None -> None; anything else ValueError."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ValueError("year must be a number")
    y = int(v)
    if not 1900 <= y <= 2100:
        raise ValueError("year out of range")
    return y
