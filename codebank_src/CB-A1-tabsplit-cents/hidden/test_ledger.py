import random

from tabsplit import Ledger


def _oracle_even(total, members):
    sign = -1 if total < 0 else 1
    base, extra = divmod(abs(total), len(members))
    return {m: sign * (base + (i < extra)) for i, m in enumerate(members)}


def test_issue_scenario():
    led = Ledger()
    led.add_expense("ana", "100.00", ["ana", "ben", "cy"])
    led.add_expense("ben", "19.99", ["ben", "cy"])
    led.add_expense("cy", "-20.00", ["ana", "ben"])
    bal = led.balances()
    assert bal == {"ana": 7666, "ben": -1334, "cy": -6332}
    assert sum(bal.values()) == 0


def test_random_ledgers_balance_and_match_rules():
    rnd = random.Random(2026)
    people = ["ana", "ben", "cy", "dee", "eli"]
    for _ in range(200):
        led, want = Ledger(), {}
        for _ in range(rnd.randint(1, 12)):
            payer = rnd.choice(people)
            parts = rnd.sample(people, rnd.randint(1, 5))
            cents = rnd.randint(-5000, 200000)
            text = ("-" if cents < 0 else "") + f"{abs(cents) // 100:,}.{abs(cents) % 100:02d}"
            led.add_expense(payer, text, parts)
            want[payer] = want.get(payer, 0) + cents
            for p, s in _oracle_even(cents, parts).items():
                want[p] = want.get(p, 0) - s
        assert led.balances() == want
