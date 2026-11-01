"""The monthly reconciliation report (README "Monthly report").

One block per calendar month that has a bank line or a receipt, oldest
first. The summary line is::

    2026-03 bank 812 matched 790 open 1,204.10 | receipts 845 open 31 233.70 voided 4 cancelled 6

bank = bank lines dated in the month, matched = how many of them are
matched (single or pair), open = the sum of the open ones; receipts =
receipts dated in the month (all of them), then the number and sum of the
receipts that are open (their ref is neither matched nor cancelled, and the
amount is not 0), the number of voided receipts (amount 0) and the number
of receipts whose ref was cancelled (refunds and sales). Then up to five
lines for the open bank lines of the month with the largest absolute amount
(ties: earlier date, then lower id)::

      ! 2026-03-14 #88123 -1,200.00 TRANSFER RENT

(two spaces, "!", the ISO date, "#" and the id, the amount, the memo; a
line with an empty memo ends after the amount). Every line ends in "\\n".
"""
from .money import fmt

TOP = 5


def month_key(d) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def monthly_report(bank, receipts, result) -> str:
    matched_bank = {s[0] for s in result.singles} | {p[0] for p in result.pairs}
    matched_refs = {s[1] for s in result.singles} | {x for p in result.pairs for x in p[1:]}
    cancelled_refs = {x for c in result.cancelled for x in c}
    mb, mr = {}, {}
    for t in bank:
        mb.setdefault(month_key(t.day), []).append(t)
    for r in receipts:
        mr.setdefault(month_key(r.day), []).append(r)
    out = []
    for m in sorted(set(mb) | set(mr)):
        b, rs = mb.get(m, []), mr.get(m, [])
        ob = [t for t in b if t.id not in matched_bank]
        orr = [r for r in rs if r.amount != 0 and r.ref not in matched_refs and r.ref not in cancelled_refs]
        voided = sum(1 for r in rs if r.amount == 0)
        cancelled = sum(1 for r in rs if r.ref in cancelled_refs)
        out.append(f"{m} bank {len(b)} matched {len(b) - len(ob)} open {fmt(sum(t.amount for t in ob))}"
                   f" | receipts {len(rs)} open {len(orr)} {fmt(sum(r.amount for r in orr))} voided {voided}"
                   f" cancelled {cancelled}\n")
        for t in sorted(ob, key=lambda t: (-abs(t.amount), t.day, t.id))[:TOP]:
            line = f"  ! {t.day.isoformat()} #{t.id} {fmt(t.amount)}"
            if t.memo:
                line += f" {t.memo}"
            out.append(line + "\n")
    return "".join(out)
