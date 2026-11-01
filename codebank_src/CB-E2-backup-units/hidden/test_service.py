from _unit import SVC, one, read, span_seconds, words


def test_oneshot_command_no_restart():
    s = read(SVC)
    assert one(s, "Service", "Type") == "oneshot"
    assert one(s, "Service", "ExecStart") == "/home/alex/.local/bin/hearth-backup --quiet"
    assert one(s, "Service", "Restart") in (None, "no")
    assert "Install" not in s


def test_time_limit():
    s = read(SVC)
    limit = one(s, "Service", "TimeoutStartSec") or one(s, "Service", "TimeoutSec")  # RuntimeMaxSec has no effect on oneshot
    assert span_seconds(limit) == 7200, limit
