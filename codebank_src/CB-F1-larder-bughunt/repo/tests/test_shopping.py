from larder.recipes import Recipe
from larder.shopping import missing
from larder.store import Pantry

BREAD = Recipe("bread", 2, [("Flour", 1.25, "kg"), ("Salt", 10, "g"), ("Water", 0.8, "l")])


def test_empty_pantry():
    assert missing(Pantry(), BREAD, 2) == [("flour", 1.25, "kg"), ("salt", 10, "g"), ("water", 0.8, "l")]


def test_partial_stock():
    p = Pantry()
    p.add("flour", 750, "g")
    p.add("salt", 1, "kg")
    assert missing(p, BREAD, 2) == [("flour", 0.5, "kg"), ("water", 0.8, "l")]
