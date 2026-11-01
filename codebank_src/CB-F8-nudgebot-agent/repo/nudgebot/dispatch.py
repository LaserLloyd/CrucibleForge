from datetime import datetime
from zoneinfo import ZoneInfo

from .model import Nudge
from .quiet import send_time
from .rotation import next_assignee
from .schedule import first_due, next_due


def tick(now: datetime, config: dict, state: dict) -> list[Nudge]:
    tz = ZoneInfo(config["tz"])
    out: list[Nudge] = []
    for chore in config["chores"]:
        cs = state.setdefault("chores", {}).setdefault(chore["id"], {})
        sent = cs.setdefault("sent", [])
        due = first_due(chore, tz)
        while due <= now:
            if due not in sent:
                at = send_time(due, config.get("quiet", "00:00-00:00"), tz)
                if at > now:
                    break
                if chore.get("rotate"):
                    who = next_assignee(config["members"], cs.get("last_assignee"))
                    cs["last_assignee"] = who
                else:
                    who = "everyone"
                sent.append(due)
                out.append(Nudge(chore["id"], chore["title"], who, due, at))
            due = next_due(chore, tz, due)
    return out
