from _unit import SVC, one, read


def test_nice_and_idle_io():
    s = read(SVC)
    assert one(s, "Service", "Nice") == "10"
    assert one(s, "Service", "IOSchedulingClass") in ("idle", "3")
