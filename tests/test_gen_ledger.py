"""G3 ledger generator: determinism, seeds differ, reference 1.0, mutants < 1.0, lazy 0.0, sizes."""
import re

import pytest

from crucibleforge import graders
from crucibleforge.gen import ledger as L

BANDS = {1: (1300, 1750), 2: (2400, 2700), 3: (4300, 4650), 4: (8000, 8500), 5: (14500, 15200)}
V2 = (2, 3, 4, 5)
G = lambda lv: {"type": "ledger", "level": lv}  # noqa: E731
KW = lambda i: dict(release_seed="t-rel", case_id=f"LG-{i}")  # noqa: E731


def _checks_score(text, m):
    return graders.grade_checks(text, m["grader_config"])["rate"]


@pytest.mark.parametrize("lv", [1, 3, 5])
def test_deterministic_and_seeds_differ(lv):
    a = L.materialize(G(lv), **KW(1))
    assert a == L.materialize(G(lv), **KW(1))
    assert a["prompt"] != L.materialize(G(lv), **KW(2))["prompt"]
    assert a["prompt"] != L.materialize(G(lv), release_seed="other", case_id="LG-1")["prompt"]


@pytest.mark.parametrize("lv", sorted(L.LEVELS))
def test_reference_mutants_lazy_and_engine_agree(lv):
    for i in range(3):
        m = L.materialize(G(lv), **KW(i))
        ref = L.reference(G(lv), **KW(i))
        assert L.grade(ref, m) == 1.0 and _checks_score(ref, m) == 1.0
        muts = L.mutants(G(lv), **KW(i))
        assert len(muts) >= 3
        for x in muts + ["", "I cannot tell.", "Q1: 0\nQ2: nobody\nQ3: 0\nQ4: 0\nQ5: nobody"]:
            g = L.grade(x, m)
            assert abs(g - _checks_score(x, m)) < 1e-9, (x, g)
            if x in muts:
                assert g < 1.0
            else:
                assert g == 0.0
        # per-question credit: break exactly Q2
        lines = ref.splitlines()
        lines[1] = "Q2: Nobody"
        assert L.grade("\n".join(lines), m) == pytest.approx(0.8)
        assert _checks_score("\n".join(lines), m) == pytest.approx(0.8)


def test_answer_format_tolerance():
    m = L.materialize(G(2), **KW(0))
    ans = m["meta"]["answers"]
    text = "Here:\n**Q1:** " + f"{int(ans[0]):,}" + "\n" + "\n".join(
        f"Q{i}: {a}." for i, a in enumerate(ans[1:], 2))
    assert L.grade(text, m) == 1.0 and _checks_score(text, m) == 1.0
    # last answer line wins
    assert L.grade("Q1: 1\n" + L.reference(G(2), **KW(0)), m) == 1.0


@pytest.mark.parametrize("lv", sorted(L.LEVELS))
def test_sizes_within_docstring_table(lv):
    lo, hi = BANDS[lv]
    for i in range(30):
        m = L.materialize(G(lv), release_seed="size", case_id=f"S{i}")
        assert lo <= m["meta"]["prompt_tokens_est"] <= hi, (lv, i, m["meta"]["prompt_tokens_est"])
        assert m["meta"]["events"] == L.LEVELS[lv]["events"]
        assert len(m["grader_config"]["checks"]) == 5


def test_rules_all_fire_and_switch_mid_log():
    for lv in (3, 5):
        inst = L._instance(G(lv), "t-rel", "LG-9")
        fired = inst["_sim"]["fired"]
        assert fired["fee"] and fired["cap"] and fired["freeze"]
        first_rule = next(i for i, e in enumerate(inst["events"]) if e["k"] == "rule")
        assert first_rule > 3


# ---------------------------------------------------------------- 4.5 fixes and v2 levels
def _hand_inst():
    """Twelve events worked by hand: a fee-charged payment that is undone, a rejected payment whose
    undo does nothing, an alias, a cap that is lifted."""
    ev = [
        {"k": "rule", "rule": "fee", "arg": 5},
        {"k": "tx", "a": 0, "b": 1, "an": "Aaaa", "bn": "Bbbb", "amt": 100},   # 2: A 895, B 1100, Bank 5
        {"k": "tx", "a": 1, "b": 2, "an": "Bbbb", "bn": "Cccc", "amt": 50},    # 3: B 1045, C 1050, Bank 10
        {"k": "rule", "rule": "cap", "arg": 80},
        {"k": "tx", "a": 0, "b": 2, "an": "Aaaa", "bn": "Cccc", "amt": 90},    # 5: rejected by the cap
        {"k": "undo", "t": 2},                                                   # 6: A 1000, B 945, Bank 5
        {"k": "undo", "t": 5},                                                   # 7: rejected, does nothing
        {"k": "tx", "a": 2, "b": 0, "an": "Cccc", "bn": "Aaaa", "amt": 60},    # 8: C 985, A 1060, Bank 10
        {"k": "rule", "rule": "cap", "arg": None},
        {"k": "tx", "a": 0, "b": 1, "an": "Aaaa", "bn": "Bbbb", "amt": 200},   # 10: A 855, B 1145, Bank 15
        {"k": "alias", "who": 0, "an": "Aaaa", "name": "Zzzz"},
        {"k": "tx", "a": 0, "b": 2, "an": "Zzzz", "bn": "Cccc", "amt": 10},    # 12: A 840, C 995
    ]
    return {"base": {0: "Aaaa", 1: "Bbbb", 2: "Cccc"}, "opening": "ring with Aaaa", "events": ev}


def test_undo_reverses_hand_worked_fixture():
    inst = _hand_inst()
    sim = L.simulate(inst)
    # C: 1000 + 50 (ev3) - 60 - 5 (ev8) + 10 (ev12) = 995 ; Bank: 5 + 5 - 5 (undo) + 5 + 5 + 5 = 20
    assert sim["bal"] == {0: 840, 1: 1145, 2: 995, "Bank": 20}
    assert sim["fired"]["fee"] == [2, 3, 8, 10, 12] and sim["fired"]["cap"] == [5]
    assert sim["hold"] == {"ring": 0}
    # the pre-4.5 bug (an undo applied the payment a second time) gives different balances
    bug = L.simulate(inst, undo_reapplies=True)
    assert bug["bal"][0] != 840 and bug["bal"][1] != 1145
    assert L.simulate(inst, ignore_undo=True)["bal"][0] != 840


def _parse_log(prompt):
    body = prompt.split("\nLOG\n", 1)[1].split("\n\nQUESTIONS", 1)[0]
    op = re.search(r"Opening holders: (.*?)\.\n", prompt).group(1)
    return body.splitlines(), dict(x.split(" with ") for x in op.split(", "))


def second_sim(lines, opening, *, drop=None, smudge_amt=None):
    """Independent second simulator: reads the rendered log TEXT, shares no code with ledger.simulate.
    Returns (balance by name lookup function, holder-name lookup, first rejection per (payee name, rule))."""
    who = {}                                    # name -> account number

    def acct(name):
        if name not in who:
            who[name] = len(who)
        return who[name]
    coins, held, rej = {}, {}, []
    fee = cap = frozen = None
    effect = {}
    for it, nm in opening.items():
        held[it] = acct(nm)
    for ln in lines:
        num, rest = int(ln[:4]), ln[5:]
        if drop == num:
            continue
        if rest.startswith("rule "):
            _, rule, arg = rest.split(" ")
            val = None if arg == "off" else arg
            if rule == "fee":
                fee = None if val is None else int(val)
            elif rule == "cap":
                cap = None if val is None else int(val)
            else:
                frozen = None if val is None else acct(val)
        elif rest.startswith("undo "):
            for who_, d in effect.pop(int(rest[5:]), []):
                coins[who_] = coins.get(who_, 1000) - d
        elif rest.startswith("item "):
            _, it, pair = rest.split(" ")
            _a, b = pair.split(">")
            held[it] = acct(b)
        elif "=" in rest and ">" not in rest:
            a, c = rest.split("=")
            who[c] = acct(a)
        else:
            pair, amt = rest.split(" ")
            a, b = pair.split(">")
            pa, pb = acct(a), acct(b)
            amt = (smudge_amt or 10) if amt == "??" else int(amt)
            if frozen is not None and pb == frozen:
                rej.append((num, pb, "freeze"))
            elif cap is not None and amt > cap:
                rej.append((num, pb, "cap"))
            else:
                d = [(pa, -amt), (pb, amt)]
                if fee:
                    d += [(pa, -fee), ("bank", fee)]
                for k, x in d:
                    coins[k] = coins.get(k, 1000 if k != "bank" else 0) + x
                effect[num] = d
    return (lambda name: coins.get(acct(name), 1000)), (lambda it: held[it]), rej, acct


def _second_answers(prompt):
    lines, opening = _parse_log(prompt)
    qs = prompt.split("QUESTIONS", 1)[1]
    out = []
    bal, hold, rej, acct = second_sim(lines, opening)
    for q in re.findall(r"^Q\d\. (.*)$", qs, re.M):
        if q.startswith("What is the final balance"):
            out.append(str(bal(re.search(r"known as (\w+)\?", q).group(1))))
        elif q.startswith("Who holds"):
            out.append(("holder", hold(re.search(r"holds the (\w+) at", q).group(1)), acct))
        elif q.startswith("What is the number of the first event"):
            nm, rule = re.search(r"known as (\w+) was rejected by the (\w+) rule", q).groups()
            hit = [e for e, p, w in rej if p == acct(nm) and w == rule]
            out.append(f"{hit[0]:04d}" if hit else "0000")
        elif q.startswith("Suppose line"):
            line, nm = re.search(r"line (\d{4}) \(.*known as (\w+) be", q).groups()
            b2, *_ = second_sim(lines, opening, drop=int(line))
            out.append(str(b2(nm)))
        else:
            line, nm, val = re.search(r'Line (\d{4}) reads ".*known as (\w+) was (-?\d+)\.', q).groups()
            sols = []
            for a in range(10, 500):
                b2, *_ = second_sim(lines, opening, smudge_amt=a)
                if b2(nm) == int(val):
                    sols.append(a)
            out.append(sols)
    return out


@pytest.mark.parametrize("lv", V2)
def test_two_simulators_agree_on_all_five_answers(lv):
    for i in range({2: 30, 5: 3}.get(lv, 6)):
        m = L.materialize(G(lv), release_seed="two-sim", case_id=f"T{i}")
        got = _second_answers(m["prompt"])
        ans = m["meta"]["answers"]
        for k, (g, a) in enumerate(zip(got, ans)):
            if isinstance(g, tuple):                         # holder: the answer must be a name of that account
                assert a.lower() in [x.lower() for x in m["accept"][k]]
                assert g[2](a) == g[1], (lv, i, k)
            elif isinstance(g, list):                        # INV: exactly one amount fits, and it is the answer
                assert g == [int(a)], (lv, i, g, a)
            else:
                assert g == a, (lv, i, k, g, a)


@pytest.mark.parametrize("lv", V2)
def test_v2_reference_mutants_and_named_mutants(lv):
    for i in range(6):
        m = L.materialize(G(lv), **KW(i))
        ref = L.reference(G(lv), **KW(i))
        assert L.grade(ref, m) == 1.0 and _checks_score(ref, m) == 1.0
        assert m["max_tokens"] == 6144
        named = dict(L.mutants_named(G(lv), **KW(i)))
        kinds = m["meta"]["kinds"]
        for need in ("undo-applies-again", "ignore-undo", "ignore-rules", "alias-distinct",
                     "factual-for-CF", "confusable-swap") + (("inv-ignores-fee",) if "INV" in kinds else ()):
            assert need in named, (lv, i, need)
        own = {"factual-for-CF": kinds.index("CF"), "confusable-swap": kinds.index("BAL")}
        if "INV" in kinds:
            own["inv-ignores-fee"] = kinds.index("INV")
        for name, text in named.items():
            per = L.grade_per_question(text, m["accept"], m["numeric"])
            assert not all(per), (lv, name)
            assert abs(L.grade(text, m) - _checks_score(text, m)) < 1e-9
            if name in own:
                assert per[own[name]] is False, (lv, name)
        # the old undo bug is a wrong answer now
        assert L.grade(named["undo-applies-again"], m) < 1.0


@pytest.mark.parametrize("lv", V2)
def test_v2_structure_30_seeds(lv):
    for i in range(30):
        inst = L._instance(G(lv), "struct", f"Z{i}")
        t1, t2, t3 = inst["targets"]
        for t in inst["targets"]:
            assert 12 <= L._touching(inst, t) <= 25, (lv, i, t)
        base = inst["base"]
        evs = inst["events"]
        for v, tw in inst["twin"].items():
            a, b = base[v], base[tw]
            assert len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1      # edit distance 1
            uses = sum(1 for e in evs if e["k"] == "tx" and tw in (e["a"], e["b"]))
            assert uses >= 5 and sum(1 for e in evs if e["k"] == "tx" and v in (e["a"], e["b"])) >= 5
        sim = inst["_sim"]
        assert sim["fired"]["fee"] and sim["fired"]["cap"]
        if lv >= 3:
            assert sim["fired"]["freeze"]
            # INV: exactly one amount in 10..499, and the fee matters
            s = inst["smudge"]
            assert L._inv_candidates(inst, t3) == [evs[s - 1]["amt"]]
            assert sim["state"][s][0]
        # BAL: >= 2 rejected payments of T1 and an executed fee-charged payment of T1 that is undone
        assert sum(1 for r in sim["rejected"] if t1 in (r[1], r[2])) >= 2
        undone = {e["t"] for e in evs if e["k"] == "undo"}
        assert any(e in undone and sim["state"][e][0] and e not in {r[0] for r in sim["rejected"]}
                   for e, ev in enumerate(evs, 1) if ev["k"] == "tx" and t1 in (ev["a"], ev["b"]))
        # CF: the cap line matters, and "just add the rejected payments back" is wrong
        c1, nxt = inst["cap_window"]
        win = {r[0] for r in sim["rejected"] if t2 in (r[1], r[2]) and r[3] == "cap" and c1 < r[0] < nxt}
        assert len(win) >= 2 and win & undone
        cf = L.simulate(inst, skip={c1})["bal"][t2]
        assert cf != sim["bal"][t2]
        assert cf != L.simulate(inst, skip={c1}, ignore_undo_of=win)["bal"][t2]


def test_inv_arithmetic_matches_full_resimulation():
    for lv in (3, 4, 5):
        for i in range(3):
            inst = L._instance(G(lv), "inv-brute", f"B{i}")
            s, t3 = inst["smudge"], inst["targets"][2]
            sols = [a for a in range(10, 500)
                    if L.simulate(inst, amt_override={s: a})["bal"][t3] == inst["q"]["inv_value"]]
            assert sols == [inst["events"][s - 1]["amt"]]


def test_v2_v1_levels_unchanged_shape():
    for lv in (1,):
        m = L.materialize(G(lv), **KW(0))
        assert m["max_tokens"] == 512 and "v2" not in m["meta"]
