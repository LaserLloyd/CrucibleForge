"""IMPLIED `pay` check `backwards` (FIXNOW 17, lead 2026-11-15): a clock-out earlier than the clock-in must be rejected
(ValueError) or the shift ignored (8 h paid) or read as running past midnight (24 h in all). Before, any total from 8 h to
24 h passed, so a program that paid the reversed shift as 16 hours worked (the stored 8B answer: 24000 at 1500/h) passed
although the brief's why text rules it out. The F-055 widening for zeromax/nonpos is not touched."""
import re

import pytest

from crucibleforge.gen import implied as im


def _instances():
    out = []
    for s in range(1, 41):
        for lvl in (1, 2, 3, 4):
            try:
                inst = im.make_instance(s, lvl, "pay")
            except Exception:
                continue
            if "backwards" in [e["id"] for e in inst["hidden"]["exps"]]:
                out.append(inst)
        if len(out) >= 6:
            break
    return out


def _wrap(code, body):
    name = re.search(r"def (\w+)\(shifts, (\w+)\)", code).group(1)
    return code + f"\ndef {name}(shifts, rate):\n" + body


ABS16 = ("    m = sum(abs(s['end'] - s['start']) for s in {s['start']: s for s in shifts}.values())\n"
         "    return (2 * rate * m + 60) // 120\n")
RAISE = ("    if any(s['end'] < s['start'] for s in shifts): raise ValueError('clock-out before clock-in')\n"
         "    return _ref(shifts, rate)\n")
WRAP24 = ("    return _ref([dict(s, end=s['end'] + 1440) if s['end'] < s['start'] else s for s in shifts], rate)\n")


def _with_ref(code, body):
    name = re.search(r"def (\w+)\(shifts, (\w+)\)", code).group(1)
    return code + f"\n_ref = {name}\ndef {name}(shifts, rate):\n" + body


def _bw(inst, code):
    j = im._judge_all(inst, code)
    return next(r["pass"] for r in j["results"] if r["id"] == "backwards")


INSTS = _instances()


def test_instances_found():
    assert len(INSTS) >= 3


@pytest.mark.parametrize("inst", INSTS, ids=lambda i: str(i.get("seed", "")))
def test_reference_ignore_raise_and_wrap_pass(inst):
    ref = im.reference_code(inst)
    assert _bw(inst, ref)                        # ignores the reversed shift: 8 h
    assert _bw(inst, _with_ref(ref, RAISE))      # rejects it
    assert _bw(inst, _with_ref(ref, WRAP24))     # reads it as past midnight: 24 h in all


@pytest.mark.parametrize("inst", INSTS, ids=lambda i: str(i.get("seed", "")))
def test_paying_the_reversed_shift_as_hours_worked_fails(inst):
    assert not _bw(inst, _wrap(im.reference_code(inst), ABS16))
