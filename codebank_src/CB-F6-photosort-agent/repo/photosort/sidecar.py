"""Reading the phone's sidecar JSON."""
import json
from datetime import datetime, timezone


def read_sidecar(path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def parse_taken(sc: dict) -> datetime:
    """The capture time as an aware datetime."""
    return datetime.strptime(sc["taken"], "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
