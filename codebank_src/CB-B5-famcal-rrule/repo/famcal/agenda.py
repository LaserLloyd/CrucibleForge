"""The week view."""
from datetime import timedelta

from .rrule import expand, parse_rrule


def week(events, monday_utc):
    """events: [{"title", "dtstart", "rrule" (text or None), "exdates" (list)}].
    Returns [(utc instant, title)] for the 7 days from monday_utc, sorted."""
    end = monday_utc + timedelta(days=7)
    out = []
    for ev in events:
        if ev.get("rrule"):
            times = expand(ev["dtstart"], parse_rrule(ev["rrule"]), monday_utc, end, ev.get("exdates", ()))
        else:
            times = [ev["dtstart"]] if monday_utc <= ev["dtstart"] < end else []
        out += [(t, ev["title"]) for t in times]
    return sorted(out)
