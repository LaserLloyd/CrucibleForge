import pytest

from homehub.rules.conditions import parse_condition


@pytest.mark.parametrize("text", ["door.front == open for 10m", "temp.x < 18 for 90s",
                                  "temp.x < 18 and door.front == open for 2h", "not door.front == open for 1d"])
def test_accepted(text):
    parse_condition(text)


@pytest.mark.parametrize("text", ["time in 22:00-06:00 for 5m", "door.front == open for", "door.front == open for 10x",
                                  "door.front == open for ten", "(door.front == open) for 5m", "door.front for 5m"])
def test_rejected(text):
    with pytest.raises(ValueError):
        parse_condition(text)


def test_plain_conditions_unchanged():
    parse_condition("temp.x < 18 and time in 22:00-06:00 or (light.hall == on)")
