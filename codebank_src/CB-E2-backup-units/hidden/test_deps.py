from _unit import SVC, read, words


def test_network_online_wanted_and_ordered():
    s = read(SVC)
    assert "network-online.target" in words(s, "Unit", "Wants") + words(s, "Unit", "Requires")
    assert "network-online.target" in words(s, "Unit", "After")


def test_on_failure_alert():
    s = read(SVC)
    assert "hearth-alert@%n.service" in words(s, "Unit", "OnFailure")
