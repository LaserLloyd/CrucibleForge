from larder.store import Pantry


def test_add_and_stock():
    p = Pantry()
    p.add("Rice", 2, "KG")
    assert p.stock("rice") == (2.0, "kg")
