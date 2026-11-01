import pytest

from mealplan.fmt import amount


@pytest.mark.parametrize("x,want", [(3, "3"), (1.5, "1 1/2"), (2 / 3, "2/3"), (0.375, "3/8"), (1.47, "1 1/2"),
                                    (2.97, "3"), (0.1, "a pinch"), (0.125, "1/8"), (0.5625, "1/2"),
                                    (4.0625, "4"), (0.3125, "1/3"), (2.24, "2 1/4"), (7.9, "7 7/8"),
                                    (1.0, "1"), (0.1875, "1/4")])
def test_amounts(x, want):
    assert amount(x) == want


def test_card_uses_amounts():
    from _mp import load
    from mealplan import recipes
    from mealplan.cards import card
    from mealplan.scaling import scaled
    load()
    lines = card(scaled(recipes.get("pancakes"), 2)).splitlines()
    assert lines == ["pancakes (serves 2)", "- 125 g flour", "- 1 cup milk", "- 1 pcs eggs", "- salt",
                     "- 3/4 tbsp sugar"]
