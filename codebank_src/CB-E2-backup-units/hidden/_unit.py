"""A small systemd unit-file reader: sections -> {key: [values]} (keys repeat)."""
from pathlib import Path


def read(path):
    text = Path(path).read_text()
    sections, cur, buf = {}, None, ""
    for raw in text.splitlines():
        line = buf + raw.strip()
        buf = ""
        if line.endswith("\\"):
            buf = line[:-1] + " "
            continue
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = line[1:-1]
            sections.setdefault(cur, {})
            continue
        if "=" in line and cur is not None:
            k, v = line.split("=", 1)
            sections[cur].setdefault(k.strip(), []).append(v.strip())
    return sections


def one(sections, section, key):
    vals = [v for v in sections.get(section, {}).get(key, []) if v != ""]
    return vals[-1] if vals else None


def words(sections, section, key):
    out = []
    for v in sections.get(section, {}).get(key, []):
        out += v.split()
    return out


TRUE = {"1", "yes", "true", "on"}
SVC = "systemd/hearth-backup.service"
TMR = "systemd/hearth-backup.timer"


import re

_SPAN_UNITS = {"s": 1, "sec": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "minute": 60, "minutes": 60,
               "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600}


def span_seconds(text):
    """systemd time span -> seconds ('2h', '2 hours', '7200sec', '1h 60min', '7200'); None if unparsable.
    Every spelling accepted here was checked with `systemd-analyze timespan`."""
    if text is None:
        return None
    t = text.strip().lower()
    if re.fullmatch(r"\d+", t):
        return int(t)
    total, pos = 0, 0
    for m in re.finditer(r"\s*(\d+)\s*([a-z]+)", t):
        if m.start() != pos or m.group(2) not in _SPAN_UNITS:
            return None
        total += int(m.group(1)) * _SPAN_UNITS[m.group(2)]
        pos = m.end()
    return total if pos == len(t) and total else None


_WEEK = {"mon..sun", "mon-sun", "mon,tue,wed,thu,fri,sat,sun"}


def calendar_is_daily_0230(text):
    """OnCalendar spellings of 'every day at 02:30 local' (each checked once
    with `systemd-analyze calendar` -> '*-*-* 02:30:00')."""
    parts = text.strip().split()
    if parts and parts[0].lower() in _WEEK:
        parts = parts[1:]
    if parts and parts[0] == "*-*-*":
        parts = parts[1:]
    return len(parts) == 1 and re.fullmatch(r"0?2:30(:00)?", parts[0]) is not None
