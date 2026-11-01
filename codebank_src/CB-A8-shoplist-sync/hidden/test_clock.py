from shoplist import Replica


def test_clock_advances_past_merged_writes():
    a, b = Replica("a"), Replica("b")
    for _ in range(5):
        b.add("Eggs")                      # b: lamport 5
    a.merge(b)
    assert a.clock >= 5
    a.remove("eggs")                       # must be stamped 6 and win
    b.merge(a)
    assert b.items() == [] and a.items() == []


def test_set_qty_after_sync_wins():
    a, b = Replica("a"), Replica("b")
    for _ in range(3):
        b.add("Coffee", 1)
    a.merge(b)
    a.set_qty("coffee", 7)
    b.merge(a)
    assert b.items() == [("Coffee", 7, False)]
