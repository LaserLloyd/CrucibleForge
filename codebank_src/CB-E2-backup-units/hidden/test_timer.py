from _unit import TMR, TRUE, calendar_is_daily_0230, one, read, words


def test_calendar_and_catch_up():
    t = read(TMR)
    cal = [v for v in t.get("Timer", {}).get("OnCalendar", []) if v]
    assert cal and all(calendar_is_daily_0230(c) for c in cal), cal
    assert (one(t, "Timer", "Persistent") or "").lower() in TRUE
    assert one(t, "Timer", "Unit") in (None, "hearth-backup.service")
    assert not t.get("Timer", {}).get("OnBootSec") and not t.get("Timer", {}).get("OnUnitActiveSec")


def test_timer_installed():
    t = read(TMR)
    assert "timers.target" in words(t, "Install", "WantedBy")
