"""Reading the phone's sidecar JSON."""
import json
import re
from datetime import datetime, timedelta, timezone

_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def read_sidecar(path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def parse_taken(sc: dict) -> datetime:
    """The capture time as an aware datetime in the phone's own offset (its
    wall-clock fields are the local time as written); UTC when no offset."""
    naive = datetime.strptime(sc["taken"], "%Y:%m:%d %H:%M:%S")
    off = sc.get("offset")
    if not off:
        return naive.replace(tzinfo=timezone.utc)
    m = _OFFSET.match(off.strip())
    if not m:
        raise ValueError(f"bad offset {off!r}")
    delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
    return naive.replace(tzinfo=timezone(delta if m.group(1) == "+" else -delta))
