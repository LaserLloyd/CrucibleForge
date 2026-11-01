"""Finding the newest backup and judging its age."""
import os
from datetime import datetime, timezone
from pathlib import Path


def newest(dir_, pattern):
    """(path, mtime) of the newest matching file, or None."""
    files = [p for p in Path(dir_).glob(pattern) if p.is_file()]
    if not files:
        return None
    best = max(files, key=lambda p: p.stat().st_mtime)
    return best, best.stat().st_mtime


def age_hours(mtime, now=None):
    """Hours between the file's mtime and now (an aware datetime; default: the current time)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.astimezone()          # a naive time is local time
    made = datetime.fromtimestamp(mtime, timezone.utc)
    return (now - made).total_seconds() / 3600


def check_target(target, max_age_hours, now=None):
    """{'name', 'status': FRESH|STALE|MISSING, 'age_hours': float|None}"""
    if not os.path.isdir(target["dir"]):
        raise OSError(f"not a folder: {target['dir']}")
    found = newest(target["dir"], target["pattern"])
    if found is None:
        return {"name": target["name"], "status": "MISSING", "age_hours": None}
    age = age_hours(found[1], now)
    status = "STALE" if age > max_age_hours else "FRESH"
    return {"name": target["name"], "status": status, "age_hours": round(age, 1)}
