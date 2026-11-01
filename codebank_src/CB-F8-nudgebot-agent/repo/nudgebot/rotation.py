def next_assignee(members: list[str], last: str | None, roster: list[str] | None = None) -> str:
    if not members:
        raise ValueError("no members")
    if last in members:
        return members[(members.index(last) + 1) % len(members)]
    return members[0]
