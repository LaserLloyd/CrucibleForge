import pytest

from larder.store import Pantry


def test_add_sums_in_stored_unit():
    p = Pantry()
    p.add("Flour", 750, "g")
    p.add("flour", 0.5, "kg")
    assert p.stock("flour") == (1250.0, "g")
    p.add("FLOUR", 250, "G")
    assert p.stock("flour") == (1500.0, "g")


def test_add_wrong_family_rejected():
    p = Pantry()
    p.add("milk", 1, "l")
    with pytest.raises(ValueError):
        p.add("milk", 2, "pcs")
    assert p.stock("milk") == (1.0, "l")


def test_use_converts():
    p = Pantry()
    p.add("rice", 2, "kg")
    p.use("rice", 500, "g")
    assert p.stock("rice") == (1.5, "kg")
