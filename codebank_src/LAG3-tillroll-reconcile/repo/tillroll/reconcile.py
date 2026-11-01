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
from dataclasses import dataclass, field

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


def reconcile(bank, receipts) -> Result:
    lines = sorted(bank, key=lambda t: (t.day, t.id))
    open_r = sorted(receipts, key=lambda r: (r.day, r.id))
    res = Result()
    done = set()

    # pass 0: a refund cancels the sale it refunds
    for f in [r for r in open_r if r.amount < 0]:
        best = None
        for s in open_r:
            if s.till != f.till or s.amount != -f.amount:
                continue
            if (s.day, s.id) >= (f.day, f.id) or (f.day - s.day).days > REFUND_DAYS:
                continue
            best = s                     # receipt order: the last one is the latest
        if best is not None:
            res.cancelled.append((f.ref, best.ref))
            open_r.remove(f)
            open_r.remove(best)

    # pass 1: same day, lowest receipt id
    for t in lines:
        if t.amount == 0:
            continue
        for r in open_r:
            if r.amount != 0 and r.day == t.day and expected_amount(t, r) == t.amount:
                res.singles.append((t.id, r.ref))
                open_r.remove(r)
                done.add(t.id)
                break

    # pass 2: inside the window, nearest first
    for t in lines:
        if t.amount == 0 or t.id in done:
            continue
        best = None
        for r in open_r:
            if r.amount == 0:
                continue
            off = _offset(t, r)
            if not _in_window(off) or expected_amount(t, r) != t.amount:
                continue
            key = (abs(off), r.day, r.id)
            if best is None or key < best[0]:
                best = (key, r)
        if best is not None:
            res.singles.append((t.id, best[1].ref))
            open_r.remove(best[1])
            done.add(t.id)

    # pass 3: two receipts of one till on one date
    for t in lines:
        if t.amount == 0 or t.is_card or t.id in done:
            continue
        best = None
        for i, a in enumerate(open_r):
            if a.amount == 0:
                continue
            off = _offset(t, a)
            if not _in_window(off):
                continue
            for b in open_r[i + 1:]:
                if b.amount == 0 or b.day != a.day or b.till != a.till:
                    continue
                if a.amount + b.amount != t.amount:
                    continue
                key = (abs(off), a.day, min(a.id, b.id), max(a.id, b.id))
                if best is None or key < best[0]:
                    best = (key, a, b)
        if best is not None:
            _, a, b = best
            res.pairs.append((t.id, a.ref, b.ref))
            open_r.remove(a)
            open_r.remove(b)
            done.add(t.id)

    res.open_bank = [t.id for t in lines if t.id not in done]
    res.open_receipts = [r.ref for r in open_r]
    return res
