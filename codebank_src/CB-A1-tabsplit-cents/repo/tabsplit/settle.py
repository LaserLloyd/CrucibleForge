"""Turn balances into transfers."""


def settle(balances: dict[str, int]) -> list[tuple[str, str, int]]:
    """Transfers (debtor, creditor, cents) that zero every balance.

    Repeatedly the person who owes the most pays the person who is owed the
    most (ties: alphabetical), as much as one of them can close. Raises
    ValueError if the balances do not sum to zero.
    """
    if sum(balances.values()) != 0:
        raise ValueError("balances do not sum to zero")
    owe = {p: -b for p, b in balances.items() if b < 0}
    owed = {p: b for p, b in balances.items() if b > 0}
    out = []
    while owe:
        d = min(owe, key=lambda p: (-owe[p], p))
        c = min(owed, key=lambda p: (-owed[p], p))
        amt = min(owe[d], owed[c])
        out.append((d, c, amt))
        for book, who in ((owe, d), (owed, c)):
            book[who] -= amt
            if book[who] == 0:
                del book[who]
    return out
