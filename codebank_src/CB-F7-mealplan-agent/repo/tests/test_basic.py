from pathlib import Path

from mealplan import recipes
from mealplan.cards import card
from mealplan.scaling import scaled

recipes.load_recipes(Path(__file__).resolve().parent.parent / "recipes.json")


def test_card():
    assert card(recipes.get("soup")).splitlines()[1] == "- 300 g Carrots"


def test_scaled_values():
    assert scaled(recipes.get("soup"), 4)["items"][0][1] == 600
