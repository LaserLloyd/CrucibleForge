"""Matching bank lines to receipts (README "Reconciliation rules").

The accountant diffs every run against the previous output, so the rules
below are exactly what this module does, including every tie-break:

* receipts are kept in (date, id) order; receipts with the same date and id
  (two tills, or a row the till export repeated) stay in export order, and
  "the first" receipt always means the first in that order;
* pass 0: every refund, in that order, cancels the latest open sale of the
  same till with the opposite amount that comes before it in that order (an
  earlier date, or the same date and a lower id) and is at most
  REFUND_DAYS days older;
* bank lines are then processed in statement order (date, then id), in
  three passes: same day, window, pairs;
* the window is from BEFORE days before the bank date to AFTER days after;
  distance = abs(receipt date - bank date) in days;
* a card line ("CARD " memo) expects receipt amount - card_fee(amount);
* amount 0 (bank line or receipt) never matches;
* pass 1 takes the first matching receipt of the same date (lowest id);
  pass 2 the smallest distance, then the earlier receipt date, then the
  lowest id; pass 3 (not for card lines) looks for two receipts of one till
  on one date and prefers the smallest distance, then the earlier date, then
  the lowest lower id, then the lowest higher id;
* on a full tie the first receipt (or pair, by its first receipt, then its
  second) in receipt order wins.
"""
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta

from .money import percent_half_up

BEFORE = 3
AFTER = 1
REFUND_DAYS = 30


def card_fee(amount: int) -> int:
    """The terminal's fee on a sale: 1.4 % rounded half up, at least 5 cents.
    Refunds (amount <= 0) carry no fee."""
    if amount <= 0:
        return 0
    return max(5, percent_half_up(amount, 14))


def expected_amount(line, receipt) -> int:
    """What the bank line must show for this receipt."""
    if line.is_card:
        return receipt.amount - card_fee(receipt.amount)
    return receipt.amount


def _offset(line, receipt) -> int:
    return (receipt.day - line.day).days


def _in_window(off: int) -> bool:
    return -BEFORE <= off <= AFTER


@dataclass
class Result:
    cancelled: list = field(default_factory=list)  # (refund ref, sale ref)
    singles: list = field(default_factory=list)    # (bank_id, receipt ref)
    pairs: list = field(default_factory=list)      # (bank_id, ref, ref) in receipt order
    open_bank: list = field(default_factory=list)  # bank ids
    open_receipts: list = field(default_factory=list)  # refs, in receipt order


#: day offsets in preference order: distance, then the earlier date
_OFFSETS = sorted(range(-BEFORE, AFTER + 1), key=lambda o: (abs(o), o))


def reconcile(bank, receipts) -> Result:
    """Same results as the original quadratic version (see the module
    docstring); receipts are handled by POSITION in receipt order, because
    neither ids nor refs are unique."""
    lines = sorted(bank, key=lambda t: (t.day, t.id))
    rs = sorted(receipts, key=lambda r: (r.day, r.id))      # stable: export order on ties
    n = len(rs)
    alive = bytearray(b"\x01") * n
    res = Result()
    done = set()

    # pass 0: refunds, per (till, amount) list of sale positions (receipt order)
    sales = {}
    for i, r in enumerate(rs):
        if r.amount > 0:
            k = ("", r.amount)
            if k not in sales:
                sales[k] = ([], [])
            sales[k][0].append((r.day, r.id))
            sales[k][1].append(i)
    for i, f in enumerate(rs):
        if f.amount >= 0:
            continue
        b = sales.get(("", -f.amount))
        if not b:
            continue
        keys, pos = b
        j = bisect_left(keys, (f.day, f.id)) - 1      # the last sale strictly before the refund
        if j >= 0 and (f.day - keys[j][0]).days <= REFUND_DAYS:
            s = pos[j]
            del keys[j], pos[j]
            alive[i] = alive[s] = 0
            res.cancelled.append((f.ref, rs[s].ref))

    # passes 1 + 2: buckets (amount, date) -> positions in receipt order
    gross, net = {}, {}
    for i, r in enumerate(rs):
        if alive[i] and r.amount != 0:
            gross.setdefault((r.amount, r.day), deque()).append(i)
            net.setdefault((r.amount - card_fee(r.amount), r.day), deque()).append(i)

    def take(t, offsets):
        index = net if t.is_card else gross
        for off in offsets:
            q = index.get((t.amount, t.day + timedelta(days=off)))
            if not q:
                continue
            while q and not alive[q[0]]:
                q.popleft()
            if q:
                i = q.popleft()
                alive[i] = 0
                res.singles.append((t.id, rs[i].ref))
                done.add(t.id)
                return

    for t in lines:
        if t.amount != 0:
            take(t, (0,))
    for t in lines:
        if t.amount != 0 and t.id not in done:
            take(t, _OFFSETS)

    # pass 3: pairs. (day, till) -> {amount: open positions in receipt order}.
    # For a fixed date the best pair of two amounts x, y of one till is the
    # first open receipt of either amount plus the first of the other (or the
    # first two of x when x == y), so only DISTINCT amounts are scanned.
    pool, tills_of_day = {}, {}
    for i, r in enumerate(rs):
        if alive[i] and r.amount != 0:
            pool.setdefault((r.day, r.till), {}).setdefault(r.amount, []).append(i)
            tills_of_day.setdefault(r.day, {})[r.till] = None

    def best_pair(day, total):
        best = None
        for till in tills_of_day.get(day, ()):
            amounts = pool.get((day, till))
            if not amounts:
                continue
            for x, lx in amounts.items():
                y = total - x
                if y < x:
                    continue
                if y == x:
                    if len(lx) < 2:
                        continue
                    pa, pb = lx[0], lx[1]
                else:
                    ly = amounts.get(y)
                    if not ly:
                        continue
                    pa, pb = (lx[0], ly[0]) if lx[0] < ly[0] else (ly[0], lx[0])
                cand = (rs[pa].id, rs[pb].id, pa, pb)
                if best is None or cand < best:
                    best = cand
        return best

    for t in lines:
        if t.amount == 0 or t.is_card or t.id in done:
            continue
        found = None
        for off in _OFFSETS:
            found = best_pair(t.day + timedelta(days=off), t.amount)
            if found:
                break
        if found:
            _, _, pa, pb = found
            for p in (pa, pb):
                alive[p] = 0
                r = rs[p]
                amounts = pool[(r.day, r.till)]
                q = amounts[r.amount]
                del q[bisect_left(q, p)]
                if not q:
                    del amounts[r.amount]
            res.pairs.append((t.id, rs[pa].ref, rs[pb].ref))
            done.add(t.id)

    res.open_bank = [t.id for t in lines if t.id not in done]
    res.open_receipts = [rs[i].ref for i in range(n) if alive[i]]
    return res
