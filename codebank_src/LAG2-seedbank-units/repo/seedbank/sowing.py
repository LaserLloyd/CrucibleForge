"""Sowing windows relative to the last spring frost.

Each crop family has a window in weeks relative to the average last frost
date (negative = before it): indoor starts and direct sowing outside.
"""
from datetime import date, timedelta

WINDOWS = {
    #             indoors (from, to)   outdoors (from, to)
    "solanum":   ((-8, -6),            None),
    "cucurbit":  ((-4, -2),            (1, 4)),
    "brassica":  ((-6, -4),            (-4, 8)),
    "legume":    (None,                (-2, 10)),
    "allium":    ((-10, -8),           (-4, 0)),
    "apiaceae":  (None,                (-3, 12)),
    "asteraceae": ((-6, -3),           (-2, 16)),
    "amaranth":  (None,                (-3, 10)),
}


def _span(frost, weeks):
    if weeks is None:
        return None
    a, b = weeks
    return (frost + timedelta(weeks=a), frost + timedelta(weeks=b))


def windows(family, last_frost):
    """{'indoors': (start, end) | None, 'outdoors': (start, end) | None} for a family."""
    w = WINDOWS.get(family)
    if w is None:
        return {"indoors": None, "outdoors": None}
    return {"indoors": _span(last_frost, w[0]), "outdoors": _span(last_frost, w[1])}


def what_to_sow(families, last_frost, today):
    """Sorted (family, where) pairs whose window contains today (inclusive)."""
    out = []
    for fam in sorted(set(families)):
        for where, span in windows(fam, last_frost).items():
            if span and span[0] <= today <= span[1]:
                out.append((fam, where))
    return out


def parse_frost(text, year):
    """'04-20' (month-day) -> date(year, 4, 20)."""
    m, d = text.split("-")
    return date(year, int(m), int(d))
