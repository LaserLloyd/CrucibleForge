from _h import at, fired, hub_with, tick


def test_cooldown_applies_to_ticks():
    hub = hub_with("door.front == open for 1m", cooldown="1h")
    d = hub.devices["front"]
    d.contact(True, at=at(9, 0))
    tick(hub, at(9, 0), at(9, 1))
    d.contact(False, at=at(9, 2))
    d.contact(True, at=at(9, 3))
    tick(hub, at(9, 4), at(9, 5))
    assert fired(hub) == [at(9, 1).isoformat()]
    d.contact(False, at=at(10, 2))
    d.contact(True, at=at(10, 3))
    tick(hub, at(10, 4), at(10, 5))
    assert fired(hub) == [at(9, 1).isoformat(), at(10, 5).isoformat()]


def test_rules_without_for_ignore_ticks():
    hub = hub_with("door.front == open")
    hub.devices["front"].contact(True, at=at(9, 0))
    tick(hub, at(9, 0), at(9, 30))
    assert fired(hub) == [at(9, 0).isoformat()]
