"""Units and their families."""
FAMILY = {"g": ("mass", 1), "kg": ("mass", 1000), "ml": ("volume", 1), "l": ("volume", 1000),
          "tsp": ("volume", 5), "tbsp": ("volume", 15), "cup": ("volume", 240), "pcs": ("count", 1)}


def to_base(qty, unit):
    """(qty in the family's base unit, base unit) e.g. (2, "cup") -> (480, "ml")."""
    fam, k = FAMILY[unit]
    return qty * k, {"mass": "g", "volume": "ml", "count": "pcs"}[fam]
