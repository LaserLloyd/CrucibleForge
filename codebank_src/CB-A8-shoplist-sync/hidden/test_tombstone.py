from shoplist import Replica


def test_remove_unknown_item_blocks_older_add():
    a, b = Replica("a"), Replica("b")
    a.add("Rice")                          # a: lamport 1, not yet synced
    b.add("x"); b.add("y")                 # b: lamport 2
    b.remove("rice")                       # b never saw rice: lamport 3 tombstone
    b.merge(a)
    assert [n for n, _, _ in b.items()] == ["x", "y"]


def test_normalised_keys():
    a = Replica("a")
    a.add("  Oat   Milk ", 2)
    a.set_qty("oat milk", 4)
    a.check("OAT MILK")
    assert a.items() == [("Oat Milk", 4, True)]
    a.remove("oat\tmilk")
    assert a.items() == []
