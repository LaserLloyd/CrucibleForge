from datetime import timedelta

from _night import U, house, local


def test_open_door_all_night():
    hub = house()
    t, end = local(2026, 10, 17, 22, 10), local(2026, 10, 18, 7, 30)
    readings = {local(2026, 10, 17, 23, 0): 17.5, local(2026, 10, 17, 23, 0, 20): 17.5,
                local(2026, 10, 17, 23, 5): 17.4}
    while t < end:
        hub.devices["front"].contact(True, at=t.astimezone(U))
        for at, c in sorted(readings.items()):
            if t <= at < t + timedelta(minutes=5):
                hub.devices["living"].reading(c, at=at.astimezone(U))
        if t == local(2026, 10, 18, 3, 0):
            hub.devices["smoke"].contact(True, at=t.astimezone(U))
        hub.tick(t.astimezone(U), (t + timedelta(minutes=5)).astimezone(U))
        t += timedelta(minutes=5)
    sent = [(at, txt) for at, txt in hub.notifier.sent]
    assert sent == [(local(2026, 10, 18, 3, 0).astimezone(U).isoformat(), "SMOKE ALARM"),
                    (local(2026, 10, 18, 7, 0).astimezone(U).isoformat(),
                     "While you slept: Front door is open; Heating on")]
    assert sorted(n for n, _ in hub.engine.fired) == ["door-open", "heat-up", "smoke"]
