"""Recipes: ingredient lists for a number of servings."""
from dataclasses import dataclass, field


@dataclass
class Recipe:
    name: str
    servings: int
    ingredients: list = field(default_factory=list)   # (item, qty, unit)

    def scaled(self, servings: int):
        """Ingredients for `servings` servings."""
        k = servings / self.servings
        return [(item.lower(), qty * k, unit) for item, qty, unit in self.ingredients]
