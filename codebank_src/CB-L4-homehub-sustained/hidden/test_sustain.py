from _h import at, fired, hub_with, tick


def test_fires_after_duration_on_event_or_tick():
    hub = hub_with("door.front == open for 10m")
    d = hub.devices["front"]
    d.contact(True, at=at(12, 0))
    d.contact(True, at=at(12, 5))
    assert fired(hub) == []
    tick(hub, at(12, 9), at(12, 10))
    assert fired(hub) == [at(12, 10).isoformat()]
    d.contact(True, at=at(12, 15))
    tick(hub, at(12, 15), at(12, 16))
    assert len(fired(hub)) == 1                       # still the same open period: rising edge only


def test_closing_resets_the_clock():
    hub = hub_with("door.front == open for 10m")
    d = hub.devices["front"]
    d.contact(True, at=at(12, 0))
    d.contact(False, at=at(12, 8))
    d.contact(True, at=at(12, 9))
    tick(hub, at(12, 15), at(12, 16))
    assert fired(hub) == []
    tick(hub, at(12, 18), at(12, 19))
    assert fired(hub) == [at(12, 19).isoformat()]


def test_event_after_duration_fires_at_event_time():
    hub = hub_with("door.front == open for 10m")
    d = hub.devices["front"]
    d.contact(True, at=at(12, 0))
    d.contact(True, at=at(12, 11))
    assert fired(hub) == [at(12, 11).isoformat()]
