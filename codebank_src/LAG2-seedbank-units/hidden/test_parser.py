import random

import pytest
from _seedfix import format_oracle, random_quantity, triple

from seedbank.quantity import UNITS, Amount, QuantityError, format_amount, parse_quantity

GOOD = {
    "2 packets": (2000, "packet", False), "about 30 seeds": (30000, "seed", True),
    "1.5oz": (42524, "gram", False), "0.5 oz": (14175, "gram", False), "half packet": (500, "packet", False),
    "half a packet": (500, "packet", False), "half an ounce": (14175, "gram", False),
    "half a dozen seeds": (6000, "seed", False), "half dozen": (6000, "seed", False),
    "a dozen": (12000, "seed", False), "dozen seeds": (12000, "seed", False),
    "~3": (3000, "seed", True), "~  3 seeds": (3000, "seed", True), "Approx. 4 G": (4000, "gram", True),
    "approx 4 g": (4000, "gram", True), "ca. 2 pkts": (2000, "packet", True), "around ten": (10000, "seed", True),
    "1/3 packet": (333, "packet", False), "2/3 packet": (667, "packet", False), "1 1/2 pkts": (1500, "packet", False),
    "2pkts": (2000, "packet", False), "30g": (30000, "gram", False), "1,25 g": (1250, "gram", False),
    "1 kg": (1000000, "gram", False), "0.0005 g": (1, "gram", False), "0.0004 g": (0, "gram", False),
    "an ounce": (28350, "gram", False), "3 ounces": (85049, "gram", False), "12": (12000, "seed", False),
    "0 seeds": (0, "seed", False), "one sachet": (1000, "packet", False), "7 Sachets": (7000, "packet", False),
    "  TWO   PACKS ": (2000, "packet", False), "3.0 seeds": (3000, "seed", False), "1 seed": (1000, "seed", False),
    "1/8 oz": (3544, "gram", False), "1 1/16 oz": (30121, "gram", False),
}
BAD = ["half seed", "1.5", "2.5 seeds", "about ~3", "~about 3", "-3", "2 packets from Gran", "twoseeds",
       "1/0 packet", "1 1/0 g", "lots", "packet", "3 bags", "1.", ".5 g", "1 2 seeds", "about", "3 lb",
       "half", "1/2 seed"]


def test_good_inputs():
    for text, want in GOOD.items():
        assert triple(parse_quantity(text)) == want, text


def test_errors():
    for text in BAD:
        with pytest.raises(QuantityError):
            parse_quantity(text)
    assert issubclass(QuantityError, ValueError)
    try:
        parse_quantity("3 bags")
    except QuantityError as e:
        assert "3 bags" in str(e)


def test_empty_is_none():
    for text in ["", "   ", "\t \n"]:
        assert parse_quantity(text) is None


def test_format():
    assert UNITS == ("seed", "packet", "gram")
    cases = [((2000, "packet", False), "2 packets"), ((1000, "packet", False), "1 packet"),
             ((500, "packet", False), "0.5 packets"), ((1000, "seed", True), "about 1 seed"),
             ((30000, "seed", True), "about 30 seeds"), ((14175, "gram", False), "14.175 g"),
             ((1000, "gram", False), "1 g"), ((0, "seed", False), "0 seeds"), ((333, "packet", False), "0.333 packets"),
             ((1010, "gram", False), "1.01 g"), ((1000000, "gram", False), "1000 g"), ((5, "gram", True), "about 0.005 g")]
    for (m, u, ap), want in cases:
        assert format_amount(Amount(m, u, ap)) == want
    assert format_amount(None) == ""
    a = Amount(1500, "packet")
    assert (a.milli, a.unit, a.approx) == (1500, "packet", False)
    assert Amount(1500, "packet", False) == Amount(1500, "packet", False)


def test_random_grammar():
    rng = random.Random(2026)
    for _ in range(600):
        text, want = random_quantity(rng)
        if want == "error":
            with pytest.raises(QuantityError):
                parse_quantity(text)
        else:
            assert triple(parse_quantity(text)) == want, repr(text)


def test_round_trip():
    rng = random.Random(7)
    for _ in range(500):
        unit = rng.choice(UNITS)
        m = rng.randint(0, 50) * 1000 if unit == "seed" else rng.choice([rng.randint(0, 3000), rng.randint(0, 10 ** 7)])
        a = Amount(m, unit, rng.random() < 0.5)
        s = format_amount(a)
        assert s == format_oracle(m, unit, a.approx)
        assert triple(parse_quantity(s)) == (m, unit, a.approx), s
