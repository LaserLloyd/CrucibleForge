"""What to buy."""
from .units import convert


def missing(pantry, recipe, servings: int):
    """[(item, qty, unit)] still needed, in the recipe's units, sorted by item."""
    out = []
    for item, need, unit in recipe.scaled(servings):
        have = pantry.stock(item)
        have_qty = convert(have[0], have[1], unit) if have else 0
        if need - have_qty > 1e-9:
            out.append((item, round(need - have_qty, 3), unit))
    return sorted(out)
