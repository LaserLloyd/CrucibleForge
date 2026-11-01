"""The week plan and its shopping list."""
from . import recipes
from .scaling import scaled
from .units import to_base


class Week:
    def __init__(self):
        self.entries = []

    def add(self, day, recipe_name, servings):
        self.entries.append((day, recipe_name, servings))


def shopping(week):
    totals = {}
    for _day, name, servings in week.entries:
        for item_name, qty, unit in scaled(recipes.get(name), servings)["items"]:
            key = item_name.lower()
            if unit == "to taste":
                totals.setdefault((key, "to taste"), None)
                continue
            q, base = to_base(qty, unit)
            totals[(key, base)] = totals.get((key, base), 0) + q
    return sorted((k[0], None if v is None else round(v, 1), k[1]) for k, v in totals.items())
