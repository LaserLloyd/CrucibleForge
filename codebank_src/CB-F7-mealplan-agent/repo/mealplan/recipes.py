"""The recipe book."""
import json

_BOOK: dict = {}


def load_recipes(path) -> None:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    _BOOK.clear()
    for name, r in data.items():
        _BOOK[name] = {"name": name, "servings": r["servings"], "items": [list(i) for i in r["items"]]}


def get(name):
    return _BOOK[name]
