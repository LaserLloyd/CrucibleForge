"""Scaling a recipe."""


def scaled(recipe, servings):
    """A copy of `recipe` for `servings` servings (the cached recipe is never changed)."""
    k = servings / recipe["servings"]
    items = []
    for name, qty, unit in recipe["items"]:
        items.append([name, qty if unit == "to taste" else qty * k, unit])
    return {**recipe, "servings": servings, "items": items}
