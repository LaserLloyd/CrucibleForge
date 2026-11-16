"""sched evening/weekendreq: the requested free time is booked as asked (strict since 2026-11-14, scrub: "If a request has a
"start" and that time is free, use it"; the F-055 widening that also passed a FORWARD move passed a program breaking that rule).
Sonnet's stored m1imp reply (FLAT-implied1x3h_L3-002, item 2; sched seed 3, tier 3) books an out-of-hours request at the
earliest working slot from now, i.e. EARLIER than asked (Tue 20:00 -> Mon 09:00, Sat 09:00 -> Mon 09:00).
F-055 second question (2026-11-14, panel2/q4-*.md): 3 of 3 readers say that is not sound ("book the next time that works"
counts from the requested start), so both checks stay failed for it; the same code with a forward move now fails too."""
from pathlib import Path

from crucibleforge.gen import implied as I

FIX = Path(__file__).parent / "fixtures" / "implied_sched_sonnet_m1imp_L3-002_k2.py.txt"


def _res(code):
    inst = I._instance(3, 3, "sched", None)
    return {x["id"]: x["pass"] for x in I._judge_all(inst, code)["results"]}


def test_sonnet_reply_books_earlier_than_asked_and_fails():
    r = _res(FIX.read_text(encoding="utf-8"))
    assert r["evening"] is False and r["weekendreq"] is False
    assert r["x1"] and r["x2"] and r["x3"]          # the rest of the reply is graded normally


def test_same_reply_moving_forward_fails_too():
    code = FIX.read_text(encoding="utf-8")
    old = "        d = durs[i]\n        t = fit(now, d)\n"
    assert old in code
    fwd = code.replace(old, "        d = durs[i]\n        st = reqs[i].get(\"start\")\n"
                            "        t = fit(max(now, int(st)) if st is not None else now, d)\n")
    r = _res(fwd)
    assert r["evening"] is False and r["weekendreq"] is False
