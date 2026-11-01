from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
CHI = ZoneInfo("America/Chicago")


def chi(y, mo, d, h, mi=0, fold=0):
    return datetime(y, mo, d, h, mi, tzinfo=CHI, fold=fold)


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def chore(cid="c", every="daily", at="07:00", start="2026-03-01", rotate=False, title=None):
    return {"id": cid, "title": title or cid, "every": every, "at": at, "start": start, "rotate": rotate}


def cfg(*chores, quiet="00:00-00:00", members=("Ana", "Ben", "Cleo", "Dan"), tz="America/Chicago"):
    return {"tz": tz, "quiet": quiet, "members": list(members), "chores": list(chores)}


def brief(nudges):
    return [(n.chore_id, n.assignee, n.due.astimezone(UTC), n.send_at.astimezone(UTC)) for n in nudges]
