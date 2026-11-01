from shoplist import Replica


def test_same_lamport_tie_broken_by_replica_id():
    a, b = Replica("a"), Replica("b")
    a.add("Eggs", 2)
    b.add("Eggs", 6)
    x = Replica.load(a.dump()); x.merge(b)
    y = Replica.load(b.dump()); y.merge(a)
    assert x.items() == y.items() == [("Eggs", 6, False)]


def test_concurrent_add_beats_remove():
    a, b = Replica("a"), Replica("b")
    a.add("Bread"); b.add("Bread")
    a.merge(b); b.merge(a)                 # both at clock 1, synced
    a.add("Bread", 3)                      # lamport 2 at a
    b.remove("bread")                      # lamport 2 at b
    a.merge(b); b.merge(a)
    assert a.items() == b.items() == [("Bread", 3, False)]


def test_later_remove_wins():
    a, b = Replica("a"), Replica("b")
    a.add("Coffee")
    b.merge(a)
    b.remove(" COFFEE ")
    a.merge(b)
    assert a.items() == []
