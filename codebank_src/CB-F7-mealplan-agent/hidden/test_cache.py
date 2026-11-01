import copy

from _mp import load
from mealplan import recipes
from mealplan.cards import card
from mealplan.plan import Week, shopping
from mealplan.scaling import scaled


def test_scaling_never_changes_the_book():
    load()
    before = copy.deepcopy(recipes.get("pancakes"))
    s = scaled(recipes.get("pancakes"), 12)
    assert s["items"][0][1] == 750 and s["servings"] == 12
    assert recipes.get("pancakes") == before
    assert card(recipes.get("pancakes")).splitlines()[1] == "- 250 g flour"


def test_planning_twice_gives_same_list():
    load()
    w = Week()
    w.add("mon", "pancakes", 6)
    w.add("tue", "soup", 3)
    first = shopping(w)
    assert shopping(w) == first
    w2 = Week()
    w2.add("mon", "pancakes", 6)
    w2.add("tue", "soup", 3)
    assert shopping(w2) == first
