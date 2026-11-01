from datetime import timedelta

from _night import U, house, local


def test_tick_publishes_triggers_and_sends_summary():
    hub = house()
    seen = []
    hub.bus.subscribe(lambda ev: seen.append(ev.data.get("trigger")), kind="trigger")
    hub.notifier.notify("door-open", "front", "Front door is open", at=local(2026, 1, 10, 23, 0).astimezone(U))
    t = local(2026, 1, 11, 6, 58).astimezone(U)
    for _ in range(2):          # (06:58, 06:59], (06:59, 07:00]
        hub.tick(t, t + timedelta(minutes=1))
        t += timedelta(minutes=1)
    assert seen == ["morning"]
    assert [txt for _, txt in hub.notifier.sent] == ["While you slept: Front door is open"]
    assert hub.notifier.sent[0][0] == local(2026, 1, 11, 7, 0).astimezone(U).isoformat()
