"""IMPLIED level 1: scrub pass-1 coverage gaps and latent reference bugs (2026-11-15).

Each gap is a program that breaks a rule the brief states and still passed every drawn check. Every level-1 instance of the
domain now carries one check that catches it (appended after the drawn checks, drawn from nothing, so the selection and
every scenario value stay byte-identical). The latent reference bugs had no check; the key and the reference are fixed and,
where the brief does not state the rule, no check is added."""
import re

import pytest

from crucibleforge.gen import implied as im
from crucibleforge.gen import _implied_t1 as T
from crucibleforge.gen import _blackbox_ff as F

SEEDS = (1, 2, 3)


def _fails(dom, seed, mut):
    inst = im.make_instance(seed, 1, dom)
    j = im._judge_all(inst, mut(im.reference_code(inst)))
    return {r["id"] for r in j["results"] if not r["pass"]}


def _blend_ignores_b(c):
    name = re.search(r"def (\w+)\(a, b, overlap\)", c).group(1)
    return c + (f"\ndef {name}(a, b, overlap):\n    n = min(overlap, len(a)); w = [(k + 1) / (n + 1) for k in range(n)]\n"
                "    return a[:len(a) - n] + [a[len(a) - n + k] * (1 - 2 * w[k]) for k in range(n)] + b[n:]\n")


def _repeats_last(c):
    name = re.search(r"def (\w+)\(history, library\)", c).group(1)
    return c + (f"\n_orig = {name}\ndef {name}(history, library):\n    ids = [t['id'] for t in library]\n"
                f"    return history[-1] if history and history[-1] in ids else _orig(history, library)\n")


MUTANTS = [
    ("dj", _blend_ignores_b, "blendb"),
    ("dj", _repeats_last, "tempo"),
    ("invoices", lambda c: c.replace("today > due + _G", "today >= due + _G"), "dueday"),
    ("ledger", lambda c: c.replace("today - l[0] < 365)", "True)").replace('e["day"] - lot[0] >= 365 or ', ""), "oldfirst"),
    ("ledger", lambda c: c.replace("for lot in lots:", "for lot in lots[::-1]:"), "oldfirst"),
    ("ledger", lambda c: c.replace('gone = {e["id"] for e in ev if not stands(e["id"])}',
                                   "gone = {e['of'] for e in ev if e['kind'] == 'reverse'}"), "rerev"),
    ("rsvp", lambda c: c.replace('elif r["answer"] == "no": n += 1', 'elif r["answer"] == "no": n += 1; hc += _g(r)'), "noguests"),
    ("expenses", lambda c: c.replace('if l["approved"] is None: continue',
                                     'if l["approved"] is None: l = dict(l, approved=l["amount"])'), "pending"),
]


@pytest.mark.parametrize("dom,mut,check", MUTANTS, ids=[f"{d}-{c}-{i}" for i, (d, _, c) in enumerate(MUTANTS)])
def test_rule_breaking_program_fails_the_new_check(dom, mut, check):
    hit = 0
    for s in range(1, 13):
        if check not in [e["id"] for e in im.make_instance(s, 1, dom)["hidden"]["exps"]]:
            continue                       # dj `tempo` is drawn, not appended: judged where an instance carries it
        assert check in _fails(dom, s, mut), (dom, s)
        hit += 1
    assert hit >= 3


@pytest.mark.parametrize("dom", ["dj", "invoices", "rsvp", "ledger", "expenses", "meds", "grades"])
def test_reference_and_alt_pass_every_check(dom):
    for s in range(1, 9):
        inst = im.make_instance(s, 1, dom)
        codes = [im.reference_code(inst)]
        if dom in im._T.SRC or dom in im._M.SRC:
            codes.append(im.alt(inst))
        for code in codes:
            assert im._judge_all(inst, code)["q"] == 1.0, (dom, s)


def test_extras_only_at_level_1_and_appended_last():
    for s in SEEDS:
        i1, i3 = im.make_instance(s, 1, "ledger"), im.make_instance(s, 3, "ledger")
        ids1 = [e["id"] for e in i1["hidden"]["exps"]]
        assert "rerev" in ids1 and ids1[-1] == "rerev"
        assert "rerev" not in [e["id"] for e in i3["hidden"]["exps"]]


def _run_ref(dom, v, *args):
    inst = im.make_instance(1, 1, dom)
    inst["public"]["params"] = {**inst["public"]["params"], "v": v}
    code = im.reference_code(inst)
    ns = {}
    exec(compile(code, "ref", "exec"), ns)
    return ns[inst["public"]["fn"]["main"]](*args)


def test_latent_ledger_reverse_of_a_reverse():
    ev = [{"id": "e1", "day": 100, "kind": "earn", "points": 500}, {"id": "e2", "day": 101, "kind": "reverse", "of": "e1"},
          {"id": "e3", "day": 102, "kind": "reverse", "of": "e2"}]
    assert _run_ref("ledger", 0, ev, 110) == 500
    assert T._cancelled(ev) == {"e2"}


def test_latent_expenses_negative_line_pays_the_approved_credit():
    assert _run_ref("expenses", 0, [{"id": "x1", "category": "t", "amount": -1000, "approved": -500}]) == -500


def test_latent_final_grade_halves_up_on_negatives():
    inst = im.make_instance(1, 1, "grades")
    v = inst["public"]["params"]["v"]
    if v == 2:
        pytest.skip("letter-grade variant")
    items = [{"id": "a", "score": -7, "max": 10}] if v == 1 else [{"id": "a", "score": -7, "max": 10, "weight": 1}]
    assert _run_ref("grades", v, items) == -70


def test_latent_meds_tap_hours_early_does_not_cover_the_dose():
    # tz 0, local day 1: a tap at 00:00 does not mark the 08:00 dose taken at 09:00
    out = _run_ref("meds", 0, [{"name": "A", "times": [480]}], [{"med": "A", "at": 1440}], 1440 + 540, 0)
    assert [(d["med"], d["time"]) for d in out] == [("A", 480)]


def test_blackbox_float_tolerance_is_one_stored_unit():
    tols = set()
    for s in (1, 2, 3):
        for row in F.gen(s, 3)["hidden"]["exp"]:
            tols |= {t for _, _, t, _ in row if t}
    assert tols
    assert all(abs(1 / t - round(1 / t)) < 1e-9 for t in tols), tols
