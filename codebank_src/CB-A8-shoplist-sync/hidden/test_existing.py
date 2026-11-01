import pytest

from shoplist import Replica


def test_basic_ops_and_dump():
    a = Replica("a")
    a.add("Eggs", 12)
    a.add("Bread")
    a.check("bread")
    a.uncheck("bread")
    assert a.items() == [("Bread", 1, False), ("Eggs", 12, False)]
    b = Replica.load(a.dump())
    assert b.items() == a.items() and b.clock == a.clock and b.id == "a"
    with pytest.raises(ValueError):
        a.add("x", 0)
    with pytest.raises(ValueError):
        a.set_qty("x", 0)
