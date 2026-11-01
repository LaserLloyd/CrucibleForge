"""Independent oracles for the hidden tests (written from README, not from the package)."""
import calendar
import random
from datetime import date, timedelta


def o_split(total, weights):
    if total < 0:
        return [-x for x in o_split(-total, weights)]
    W = sum(weights)
    base = [(total * w) // W for w in weights]
    order = sorted(range(len(weights)), key=lambda i: (-((total * weights[i]) % W), i))
    for i in order[: total - sum(base)]:
        base[i] += 1
    return base


def o_month_shift(d, months):
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def o_occurrences(start, every, n, frm, to, until=None):
    out, k = [], 0
    while True:
        if every == "weekly":
            d = start + timedelta(days=7 * n * k)
        elif every == "monthly":
            d = o_month_shift(start, n * k)
        else:
            d = o_month_shift(start, 12 * n * k)
        if d > to or (until is not None and d > until):
            return out
        if d >= frm:
            out.append(d)
        k += 1


def fmt_plain(cents):
    s = "-" if cents < 0 else ""
    a = abs(cents)
    return f"{s}{a // 100}.{a % 100:02d}"


def fmt_commas(cents):
    s = "-" if cents < 0 else ""
    a = abs(cents)
    return f"{s}{a // 100:,}.{a % 100:02d}"


def rng(seed):
    return random.Random(seed)


# ---- year-end oracles (README "Household", "Year-end")
from fractions import Fraction
from functools import lru_cache
from itertools import combinations


def o_weights_on(base, changes, names, d):
    cur = list(base)
    for frm, w in sorted(changes, key=lambda c: c[0]):
        if frm <= d:
            for k, v in w.items():
                cur[names.index(k)] = v
    return cur


def o_shares(names, base, changes, shared):
    """shared: [(date, cents, paid_by)] -> (paid, share)"""
    paid = [0] * len(names)
    exact = [Fraction(0)] * len(names)
    for d, cents, by in shared:
        paid[names.index(by)] -= cents
        w = o_weights_on(base, changes, names, d)
        for i in range(len(names)):
            exact[i] += Fraction(-cents * w[i], sum(w))
    total = -sum(c for _, c, _ in shared)
    share = [x.numerator // x.denominator for x in exact]
    rank = sorted(range(len(names)), key=lambda i: (exact[i] - share[i], -i), reverse=True)
    for i in rank[: total - sum(share)]:
        share[i] += 1
    return paid, share


def o_settle(names, balances):
    bal = list(balances)

    @lru_cache(maxsize=None)
    def groups(rem):
        if not rem:
            return 0
        first, others = rem[0], rem[1:]
        best = None
        for k in range(len(others) + 1):
            for c in combinations(others, k):
                if bal[first] + sum(bal[j] for j in c) == 0:
                    left = tuple(j for j in others if j not in c)
                    g = 1 + groups(left)
                    best = g if best is None else max(best, g)
        return best

    rem = tuple(i for i, b in enumerate(bal) if b)
    out = []
    while rem:
        want = groups(rem)
        first, others = rem[0], rem[1:]
        chosen = None
        for k in range(len(others) + 1):
            for c in combinations(others, k):
                left = tuple(j for j in others if j not in c)
                if bal[first] + sum(bal[j] for j in c) == 0 and 1 + groups(left) == want:
                    chosen = (first,) + c
                    break
            if chosen:
                break
        work = {i: bal[i] for i in chosen}
        while True:
            deb = min(chosen, key=lambda i: (work[i], i))
            cre = min(chosen, key=lambda i: (-work[i], i))
            if work[deb] >= 0 or work[cre] <= 0:
                break
            a = min(-work[deb], work[cre])
            out.append((names[deb], names[cre], a))
            work[deb] += a
            work[cre] -= a
        rem = tuple(j for j in rem if j not in chosen)
    return out


def rand_balances(r, n):
    """n balances adding up to 0, with planted zero-sum sub-groups so the grouping matters."""
    out = []
    while len(out) < n:
        k = min(n - len(out), r.choice([1, 2, 2, 3, 3, 4]))
        if k == 1:
            out.append(0)
            continue
        part = [r.choice([1, 2, 3, 5, 10, 25]) * r.choice([1, -1]) * r.randint(1, 4) for _ in range(k - 1)]
        part.append(-sum(part))
        out += part
    r.shuffle(out)
    if r.random() < 0.3:
        i, j = r.sample(range(n), 2)
        delta = r.randint(1, 7)
        out[i] += delta
        out[j] -= delta
    return out
