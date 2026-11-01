from _h import at, fired, hub_with, tick


def test_sustained_part_tracked_while_other_part_false():
    hub = hub_with("temp.x < 18 and door.front == open for 10m", on="reading")
    hub.devices["x"].reading(20.0, at=at(12, 0))
    hub.devices["front"].contact(True, at=at(12, 0))     # door open at 12:00 while it is warm
    hub.devices["x"].reading(20.0, at=at(12, 1))          # the rule is evaluated (a reading): door still open
    hub.devices["x"].reading(17.0, at=at(12, 12))         # now cold; door open since 12:01 at the latest
    assert fired(hub) == [at(12, 12).isoformat()]


def test_or_tracks_both_sides():
    hub = hub_with("temp.x < 0 or door.front == open for 10m", on="reading")
    hub.devices["front"].contact(True, at=at(8, 0))
    hub.devices["x"].reading(-1.0, at=at(8, 1))           # left side true, right side starts its clock
    hub.devices["x"].reading(3.0, at=at(8, 5))            # left false now: rule false (door 4 min)
    hub.devices["x"].reading(3.0, at=at(8, 12))           # door open since 08:01 -> 11 min: true again
    assert fired(hub) == [at(8, 1).isoformat(), at(8, 12).isoformat()]
