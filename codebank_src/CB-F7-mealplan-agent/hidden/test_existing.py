from _mp import load
from mealplan import recipes
from mealplan.units import to_base


def test_units_and_book():
    load()
    assert to_base(2, "cup") == (480, "ml") and to_base(0.5, "kg") == (500.0, "g")
    assert recipes.get("soup")["servings"] == 2
