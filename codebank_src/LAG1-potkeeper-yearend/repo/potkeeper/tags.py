"""Free-form tags on transactions (README "Tags")."""
from collections import defaultdict


def clean(tags) -> list[str]:
    out = []
    for t in tags:
        t = str(t).strip().lower()
        if not t:
            continue
        if " " in t:
            raise ValueError(f"tag {t!r} contains a space")
        if t not in out:
            out.append(t)
    return out


def add_tags(transaction, *tags) -> None:
    transaction.tags = clean(list(transaction.tags) + list(tags))


def tagged(ledger, tag: str) -> list:
    tag = tag.strip().lower()
    return [t for t in ledger if tag in t.tags]


def tag_totals(ledger, frm, to) -> dict:
    totals = defaultdict(int)
    for t in ledger.between(frm, to):
        for tag in t.tags:
            totals[tag] += t.cents
    return dict(totals)
