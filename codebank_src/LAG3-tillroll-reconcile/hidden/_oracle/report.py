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


def fmt(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole:,}.{frac:02d}"

TOP = 5


def month_key(d) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def monthly_report(bank, receipts, result) -> str:
    matched_bank = [s[0] for s in result.singles] + [p[0] for p in result.pairs]
    matched_refs = [s[1] for s in result.singles] + [x for p in result.pairs for x in p[1:]]
    cancelled_refs = [x for c in result.cancelled for x in c]
    months = sorted({month_key(t.day) for t in bank} | {month_key(r.day) for r in receipts})
    out = ""
    for m in months:
        mb = [t for t in bank if month_key(t.day) == m]
        mr = [r for r in receipts if month_key(r.day) == m]
        ob = [t for t in mb if t.id not in matched_bank]
        orr = [r for r in mr if r.amount != 0 and r.ref not in matched_refs and r.ref not in cancelled_refs]
        voided = sum(1 for r in mr if r.amount == 0)
        cancelled = sum(1 for r in mr if r.ref in cancelled_refs)
        out += (f"{m} bank {len(mb)} matched {len(mb) - len(ob)} open {fmt(sum(t.amount for t in ob))}"
                f" | receipts {len(mr)} open {len(orr)} {fmt(sum(r.amount for r in orr))} voided {voided}"
                f" cancelled {cancelled}\n")
        worst = sorted(ob, key=lambda t: (-abs(t.amount), t.day, t.id))[:TOP]
        for t in worst:
            line = f"  ! {t.day.isoformat()} #{t.id} {fmt(t.amount)}"
            if t.memo:
                line += f" {t.memo}"
            out += line + "\n"
    return out
