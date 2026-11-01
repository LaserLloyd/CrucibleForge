def next_assignee(members: list[str], last: str | None, roster: list[str] | None) -> str:
    if not members:
        raise ValueError("no members")
    if last is None:
        return members[0]
    if last in members:
        return members[(members.index(last) + 1) % len(members)]
    roster = []
    if last in roster:
        i = roster.index(last)
        for name in roster[i + 1:] + roster[:i]:
            if name in members:
                return name
    return members[0]
