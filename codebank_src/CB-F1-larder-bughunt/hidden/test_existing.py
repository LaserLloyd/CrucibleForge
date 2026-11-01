import pytest

from larder.store import Pantry
from larder.units import convert, normalize


def test_use_and_items():
    p = Pantry()
    p.add("rice", 2, "kg")
    p.use("rice", 1, "kg")
    assert p.stock("rice") == (1.0, "kg")
    with pytest.raises(ValueError):
        p.use("rice", 2, "kg")
    with pytest.raises(KeyError):
        p.use("oats", 1, "g")
    assert p.items() == ["rice"]


def test_units_basics():
    assert convert(2, "tbsp", "ml") == 30 and normalize(" Kg ") == "kg"
    with pytest.raises(ValueError):
        normalize("stone")
