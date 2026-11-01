"""Shared helpers for the hidden tests: data generators and digests."""
import hashlib
import random
from datetime import date, timedelta

from tillroll.models import BankLine, Receipt

START = date(2025, 1, 1)
TILLS = ("front", "back", "web")


def digest(res) -> str:
    h = hashlib.sha256()
    for part in (res.cancelled, res.singles, res.pairs, res.open_bank, res.open_receipts):
        h.update(repr([tuple(x) if isinstance(x, (list, tuple)) else x for x in part]).encode())
        h.update(b"|")
    return h.hexdigest()


def text_digest(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _fee(a):
    q, r = divmod(abs(a) * 14, 1000)
    q += r * 2 >= 1000
    return 0 if a <= 0 else max(5, q)


class Tills:
    """Receipt ids are numbered per till; a crash repeats a row right after it."""

    def __init__(self, rnd, start=1, dup_rate=0.0, step=(1, 3)):
        self.rnd, self.dup, self.step = rnd, dup_rate, step
        self.next = {t: start + rnd.randrange(5) for t in TILLS}
        self.out = []

    def ring(self, day, amount, till):
        self.next[till] += self.rnd.randint(*self.step)
        r = Receipt(self.next[till], day, amount, till)
        self.out.append(r)
        if self.dup and self.rnd.random() < self.dup:
            self.out.append(r)
        return r


def _days_in_order(rnd, n, ndays):
    """Per-till ids grow with time: draw dates first, then ring in date order."""
    return sorted(rnd.randrange(ndays) for _ in range(n))


def small(seed):
    """A small adversarial statement: few amounts, few days, ties everywhere."""
    rnd = random.Random(seed)
    days = rnd.randint(1, 9)
    amounts = [rnd.choice([0, 300, 400, 700, 1000, 986, 995, 450, 5, 3, 357, 358, 360, 361])
               for _ in range(rnd.randint(1, 6))]
    ntill = rnd.randint(1, 3)
    tl = Tills(rnd, start=rnd.choice((0, 1, 100)), dup_rate=rnd.choice((0, 0.1, 0.3)), step=(1, 1) if rnd.random() < 0.5 else (1, 2))
    rec = []
    for d in _days_in_order(rnd, rnd.randint(0, 40), days):
        till = rnd.choice(TILLS[:ntill])
        if rnd.random() < 0.2 and rec:
            s = rnd.choice(rec)
            tl.ring(START + timedelta(days=d), -s.amount, s.till)
        else:
            tl.ring(START + timedelta(days=d), rnd.choice(amounts), till)
        rec = tl.out
    rec = list(tl.out)
    bank = []
    bids = rnd.sample(range(1, 400), rnd.randint(0, 30))
    for bid in bids:
        kind = rnd.random()
        if kind < 0.3 and rec:
            r = rnd.choice(rec)
            amt, memo = r.amount - _fee(r.amount), "CARD 77"
        elif kind < 0.55 and len(rec) > 1:
            a, b = rnd.sample(rec, 2)
            amt, memo = a.amount + b.amount, rnd.choice(["SHOP", "SHOP", "CARD 77"])
        else:
            amt, memo = rnd.choice(amounts + [a + b for a in amounts for b in amounts][:5]), rnd.choice(["SHOP", "", "TRANSFER"])
        bank.append(BankLine(bid, START + timedelta(days=rnd.randrange(-2, days + 2)), amt, memo))
    rnd.shuffle(bank)
    # the three till exports are concatenated: keep each till's own order
    if rnd.random() < 0.5:
        rec.sort(key=lambda r: TILLS.index(r.till))
    return bank, rec


def spread(seed, n):
    """A year of trade: many different amounts, ~a third card payouts, some refunds."""
    rnd = random.Random(seed)
    tl = Tills(rnd, dup_rate=0.003)
    for d in _days_in_order(rnd, n, 365):
        day = START + timedelta(days=d)
        if rnd.random() < 0.03 and tl.out:
            s = tl.out[-rnd.randint(1, min(len(tl.out), 3000))]
            if s.amount > 0:
                tl.ring(day, -s.amount, s.till)
                continue
        amt = rnd.randrange(-3000, 60000) if rnd.random() < 0.97 else 0
        tl.ring(day, amt, rnd.choice(TILLS))
    rec = tl.out
    bank = []
    order = list(range(len(rec)))
    rnd.shuffle(order)
    for i in range(n):
        r = rec[order[i]]
        day = r.day + timedelta(days=rnd.choice((-1, 0, 0, 0, 1, 2, 3, 4)))
        if rnd.random() < 0.35:
            bank.append(BankLine(500000 + i, day, r.amount - _fee(r.amount), "CARD 0042"))
        else:
            bank.append(BankLine(500000 + i, day, r.amount if rnd.random() < 0.9 else rnd.randrange(1, 90000),
                                 "SHOP"))
    rnd.shuffle(bank)
    return bank, rec


def hot(seed, n):
    """Coffee: most sales are the same two prices over a few days."""
    rnd = random.Random(seed)
    prices = (320, 450)
    tl = Tills(rnd, dup_rate=0.002, step=(1, 1))
    for d in _days_in_order(rnd, n, 4):
        tl.ring(START + timedelta(days=d), rnd.choice(prices), rnd.choice(TILLS))
    bank = []
    for i in range(n):
        p = rnd.choice(prices)
        day = START + timedelta(days=rnd.randrange(-1, 6))
        if rnd.random() < 0.5:
            bank.append(BankLine(900000 - i, day, p - _fee(p), "CARD 0042"))
        else:
            bank.append(BankLine(900000 - i, day, p, "SHOP"))
    return bank, tl.out


def refunds(seed, n):
    """A bad batch of cakes: many refunds of the same few prices."""
    rnd = random.Random(seed)
    prices = (1250, 1800, 2400, 990)
    tl = Tills(rnd, step=(1, 1))
    for d in _days_in_order(rnd, n, 60):
        day = START + timedelta(days=d)
        p = rnd.choice(prices)
        tl.ring(day, -p if rnd.random() < 0.3 else p, rnd.choice(TILLS[:2]))
    bank = [BankLine(i + 1, START + timedelta(days=rnd.randrange(60)), rnd.choice(prices), "SHOP")
            for i in range(n // 20)]
    return bank, tl.out


def pairs(seed, n_days, per_day, n_lines):
    """Two-item sales the bank booked as one line."""
    rnd = random.Random(seed)
    tl = Tills(rnd, dup_rate=0.002)
    for d in range(n_days):
        for _ in range(per_day):
            tl.ring(START + timedelta(days=d), rnd.randrange(100, 2500), rnd.choice(TILLS))
    rec = tl.out
    by_key = {}
    for r in rec:
        by_key.setdefault((r.day, r.till), []).append(r)
    bank = []
    for i in range(n_lines):
        a = rec[rnd.randrange(len(rec))]
        same = by_key[(a.day, a.till)]
        b = rnd.choice(same) if rnd.random() < 0.8 else None
        amt = a.amount + (b.amount if b is not None and b is not a else rnd.randrange(100, 2500))
        bank.append(BankLine(10 ** 6 + i, a.day + timedelta(days=rnd.choice((-1, 0, 1, 2, 3))), amt, "SHOP"))
    return bank, rec


def dense(seed, n_days, per_day, n_lines):
    """The bakery's own price list: 40 prices, thousands of sales a day, and
    many bank lines no two receipts add up to."""
    rnd = random.Random(seed)
    prices = sorted(rnd.sample(range(150, 2400, 10), 40))
    tl = Tills(rnd, dup_rate=0.002, step=(1, 1))
    for d in range(n_days):
        for _ in range(per_day):
            tl.ring(START + timedelta(days=d), rnd.choice(prices), rnd.choice(TILLS))
    rec = tl.out
    bank = []
    for i in range(n_lines):
        day = START + timedelta(days=rnd.randrange(-1, n_days + 1))
        if rnd.random() < 0.15:
            amt = rnd.choice(prices) + rnd.choice(prices)
        else:
            amt = rnd.randrange(100, 9000) * 2 + 1          # odd cents: never a price sum here
        bank.append(BankLine(2 * 10 ** 6 + i, day, amt, "SHOP"))
    return bank, rec
