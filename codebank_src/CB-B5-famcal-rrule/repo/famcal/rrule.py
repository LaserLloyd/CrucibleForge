"""RRULE subset: parsing (done) and expansion (to do, see docs/rrule.md)."""
import re
from datetime import datetime, timezone

DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_BYDAY = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")


def parse_rrule(text: str) -> dict:
    """'FREQ=MONTHLY;BYDAY=2TU,-1FR;COUNT=5' -> {"freq": "MONTHLY", "interval": 1,
    "byday": [(2, 1), (-1, 4)], "bymonthday": [], "count": 5, "until": None}.
    byday entries are (ordinal or None, weekday 0=Monday)."""
    out = {"freq": None, "interval": 1, "byday": [], "bymonthday": [], "count": None, "until": None}
    for part in text.strip().split(";"):
        if not part:
            continue
        key, _, value = part.partition("=")
        key = key.upper()
        if key == "FREQ":
            if value not in ("DAILY", "WEEKLY", "MONTHLY"):
                raise ValueError(f"unsupported FREQ {value!r}")
            out["freq"] = value
        elif key == "INTERVAL":
            out["interval"] = int(value)
            if out["interval"] < 1:
                raise ValueError("INTERVAL must be >= 1")
        elif key == "BYDAY":
            for item in value.split(","):
                m = _BYDAY.match(item.strip().upper())
                if not m:
                    raise ValueError(f"bad BYDAY {item!r}")
                n = int(m.group(1)) if m.group(1) else None
                if n is not None and not (1 <= abs(n) <= 5):
                    raise ValueError(f"bad BYDAY ordinal {item!r}")
                out["byday"].append((n, DAYS.index(m.group(2))))
        elif key == "BYMONTHDAY":
            for item in value.split(","):
                d = int(item)
                if d == 0 or not -31 <= d <= 31:
                    raise ValueError(f"bad BYMONTHDAY {item!r}")
                out["bymonthday"].append(d)
        elif key == "COUNT":
            out["count"] = int(value)
        elif key == "UNTIL":
            out["until"] = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        else:
            raise ValueError(f"unsupported rule part {key!r}")
    if out["freq"] is None:
        raise ValueError("FREQ is required")
    if out["count"] is not None and out["until"] is not None:
        raise ValueError("COUNT and UNTIL are exclusive")
    if out["freq"] != "MONTHLY" and any(n is not None for n, _ in out["byday"]):
        raise ValueError("ordinal BYDAY only with FREQ=MONTHLY")
    return out


def expand(dtstart, rule, after, before, exdates=()):
    """Occurrences in [after, before), see docs/rrule.md."""
    raise NotImplementedError
