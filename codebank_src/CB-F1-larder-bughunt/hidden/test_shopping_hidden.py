from larder.recipes import Recipe
from larder.shopping import missing
from larder.store import Pantry

PANCAKES = Recipe("pancakes", 4, [("Flour", 250, "g"), ("Milk", 2, "cup"), ("Eggs", 2, "pcs"), ("Sugar", 2, "tbsp")])


def test_mixed_units_scaled():
    p = Pantry()
    p.add("flour", 0.2, "kg")
    p.add("milk", 0.5, "l")
    p.add("eggs", 6, "pcs")
    p.add("sugar", 1, "tsp")
    assert missing(p, PANCAKES, 6) == [("flour", 175.0, "g"), ("milk", 0.917, "cup"), ("sugar", 2.667, "tbsp")]


def test_issue_story():
    p = Pantry()
    p.add("flour", 750, "g")
    p.add("flour", 500, "g")
    bread = Recipe("bread", 2, [("Flour", 1.25, "kg")])
    assert missing(p, bread, 2) == []
    assert missing(p, bread, 4) == [("flour", 1.25, "kg")]
