from pathlib import Path

from mealplan import recipes


def load():
    recipes.load_recipes(Path("recipes.json"))
