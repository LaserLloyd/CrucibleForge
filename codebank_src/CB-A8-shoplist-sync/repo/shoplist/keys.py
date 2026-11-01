"""Item keys."""


def item_key(name: str) -> str:
    return " ".join(name.split()).casefold()
