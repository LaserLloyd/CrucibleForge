from datetime import datetime
from zoneinfo import ZoneInfo

from .model import Nudge
from .quiet import send_time
from .rotation import next_assignee
from .schedule import last_due


def tick(now: datetime, config: dict, state: dict) -> list[Nudge]:
    tz = ZoneInfo(config["tz"])
    chores_state = state.setdefault("chores", {})
    out: list[Nudge] = []
    for chore in config["chores"]:
        due = last_due(chore, tz, now)
        if due is None:
            continue
        cs = chores_state.setdefault(chore["id"], {})
        if cs.get("last_due") is not None and cs["last_due"] >= due:
            continue
        if chore.get("rotate"):
            who = cs.get("pending_who") or next_assignee(config["members"], cs.get("last_assignee"), cs.get("roster"))
        else:
            who = "everyone"
        at = send_time(due, config.get("quiet", "00:00-00:00"), tz)
        if at > now:
            cs["pending_who"] = who
            continue
        cs.pop("pending_who", None)
        if chore.get("rotate"):
            cs["last_assignee"] = who
            cs["roster"] = list(config["members"])
        cs["last_due"] = due
        out.append(Nudge(chore["id"], chore["title"], who, due, at))
    return out
