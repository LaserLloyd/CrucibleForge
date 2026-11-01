"""Who does which chore (see README)."""


def assign(chores, members, week, away=frozenset()):
    if not chores or not members or len(set(chores)) != len(chores) or len(set(members)) != len(members):
        raise ValueError("chores and members must be non-empty lists of distinct names")
    n = len(members)
    present = [m for m in members if m not in away]
    taken = set()
    out = {}
    for i, chore in enumerate(chores):
        start = (i + week) % n
        order = [members[(start + k) % n] for k in range(n)]
        pick = next((m for m in order if m not in away), None)
        if pick is None and present:
            pick = next(m for m in order if m not in away)
        out[chore] = pick
        if pick is not None:
            taken.add(pick)
    return out


def schedule(chores, members, weeks, away_by_week=None):
    away_by_week = away_by_week or {}
    return [(w, assign(chores, members, w, away_by_week.get(w, set()))) for w in weeks]
