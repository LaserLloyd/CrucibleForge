"""Scaling a recipe."""


def scaled(recipe, servings):
    """A copy of `recipe` for `servings` servings."""
    k = servings / recipe["servings"]
    out = dict(recipe)
    out["servings"] = servings
    for item in out["items"]:
        if item[2] != "to taste":
            item[1] = item[1] * k
    return out
