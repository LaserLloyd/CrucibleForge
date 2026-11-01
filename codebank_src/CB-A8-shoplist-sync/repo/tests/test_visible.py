from shoplist import Replica


def test_add_and_sync():
    a, b = Replica("a"), Replica("b")
    a.add("Oat Milk", 2)
    b.merge(a)
    assert b.items() == [("Oat Milk", 2, False)]


def test_round_trip():
    a = Replica("a")
    a.add("eggs", 12)
    a.check("eggs")
    assert Replica.load(a.dump()).state() == a.state()
