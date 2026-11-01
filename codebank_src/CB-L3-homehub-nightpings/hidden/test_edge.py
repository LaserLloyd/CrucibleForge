from _night import U, house, local


def test_heartbeats_fire_once():
    hub = house()
    for m in range(0, 60, 5):
        hub.devices["front"].contact(True, at=local(2026, 1, 10, 12, m).astimezone(U))
    assert [n for n, _ in hub.engine.fired] == ["door-open"]
    hub.devices["front"].contact(False, at=local(2026, 1, 10, 13, 0).astimezone(U))
    hub.devices["front"].contact(True, at=local(2026, 1, 10, 13, 5).astimezone(U))
    assert [n for n, _ in hub.engine.fired] == ["door-open", "door-open"]


def test_other_devices_events_do_not_refire():
    hub = house()
    hub.devices["front"].contact(True, at=local(2026, 1, 10, 12, 0).astimezone(U))
    hub.devices["living"].reading(17.0, at=local(2026, 1, 10, 12, 1).astimezone(U))   # sets the boiler: a state event
    hub.devices["living"].reading(16.0, at=local(2026, 1, 10, 12, 9).astimezone(U))
    assert sorted(n for n, _ in hub.engine.fired) == ["door-open", "heat-up"]
