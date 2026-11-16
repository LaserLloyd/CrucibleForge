"""ledger (G3): state tracking and needle-finding over a long generated log.

An instance seed yields a log of coin transfers, undos, name aliases, item
hand-overs and rules that switch on and off mid-log, plus distractor accounts,
and five exact-answer questions about the final state. Everything is computed
by simulating the log (the reference); no judge, no keyword counting.

Grading: one point per question (5 checkpoints), exact match on an answer line
``Qn: <answer>`` (the LAST such line counts). The engine scores it with the
``checks`` grader (one anchored regex per question); ``grade()`` is the same
rule written independently and the tests assert both agree.

Questions, levels 1 and 5 (global): Q1 final balance of an account (asked by an alias when one
exists), Q2 who holds item Y at the end, Q3 number of the first event that fired a rule, Q4 how many
events fired the fee rule, Q5 which account ends with the highest balance.

4.5 "v2" levels 2-4 (S8-DESIGN section 3): every question is bounded, naming the accounts it needs
(each target has 12-25 touching events among 400-1600 lines, next to confusable names one letter
apart). Q1 BAL final balance of T1 asked by an alias chain (two cap/freeze rejections and an undone,
fee-charged payment); Q2 HOLD an item handed over through a confusable twin to an aliased account;
Q3 FIRST first event where a payment to X was rejected by the freeze (L3-4) or cap (L2) rule;
Q4 CF counterfactual "line N (rule cap K) had never been written" for T2 (one rejected payment is later
undone: it now executes, then the undo reverses it); Q5 INV the smudged amount of T3's payment given
T3's final balance (unique in 10..499, fee and cap included; L2 asks BAL2 instead). v2 cases send
max_tokens 6144. 4.5 fix: an undo REVERSES the payment (the first 4.5 build re-applied it).

Level table (events = log lines; prompt tokens = chars/4 of the whole prompt, 30 seeds per level;
real tokenizers count digit-heavy lines about 3x higher):
  level  events  accounts(+distractors)  aliases  confusable pairs  undos  rules in the log   prompt tokens
    1      200      6+4                    2         -              ~6%    fee                 ~1.5k
    2      400      8+8                    4         1              ~7%    fee, cap            ~2.55k
    3      800     10+14                   6         2              ~8%    fee, cap, freeze    ~4.5k
    4     1600     12+22                   9         3              ~9%    + fee off, cap/freeze move ~8.2k
    5     3000     14+30                  12         -              ~10%   + second fee, cap lifted   ~14.7k
The bands are asserted in tests/test_gen_ledger.py. L1 sits below the plan's 2k floor on purpose.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys

from .. import gen as _gen  # noqa: E402  (one seed derivation: gen.instance_seed)

FAMILY = "ledger"

# rule schedule entries: (fraction of the log, rule, arg) ; arg None = off
_SCHED = {
    1: [(0.30, "fee", 5)],
    2: [(0.30, "fee", 5), (0.50, "cap", 250)],
    3: [(0.25, "fee", 5), (0.45, "cap", 250), (0.60, "freeze", "@hub")],
    4: [(0.20, "fee", 5), (0.35, "cap", 260), (0.45, "freeze", "@hub"), (0.65, "fee", None),
        (0.75, "cap", 200), (0.85, "freeze", "@hub2")],
    5: [(0.15, "fee", 5), (0.30, "cap", 260), (0.40, "freeze", "@hub"), (0.55, "fee", None),
        (0.62, "cap", 200), (0.72, "freeze", "@hub2"), (0.80, "fee", 7), (0.92, "cap", None)],
}
LEVELS = {
    1: dict(events=200, core=6, distract=4, aliases=2, undo=0.06),
    2: dict(events=400, core=8, distract=8, aliases=4, undo=0.07),
    3: dict(events=800, core=10, distract=14, aliases=6, undo=0.08),
    4: dict(events=1600, core=12, distract=22, aliases=9, undo=0.09),
    5: dict(events=3000, core=14, distract=30, aliases=12, undo=0.10),
}

# median reference-answer tokens (chars/4, 30 seeds; tests/test_gen_answer_tokens.py re-measures)
_ANSWER_TOKENS = {1: 11, 2: 11, 3: 11, 4: 11, 5: 11}
for _lv, _row in LEVELS.items():
    _row["answer_tokens"] = _ANSWER_TOKENS[_lv]

_ITEMS = ["ring", "lamp", "key", "map", "coin", "book", "drum", "flag"]
START = 1000

_PREAMBLE = """You are given the log of a small club's accounts. Read it carefully and answer the questions after it.

HOW THE LOG WORKS
- Every account starts with 1000 coins. The account "Bank" starts with 0. Balances may go negative.
- Each line is "<event number> <event>". Events happen in numeric order.
- "A>B n" : A pays B n coins. A and B may be written with any name the account has at that moment.
- "undo N" : reverses the payment made in event N exactly as it was executed (including any fee it paid). If event N did nothing (it was rejected), undo N does nothing. An event is undone at most once.
- "A=C" : from this event on, C is another name for the account A (A may itself be a name). The old name still works.
- "item X A>B" : A hands the item X to B. Items are not coins and no rule affects them. Opening holders: {opening}.
- "rule fee K" : from now on every payment that is executed also moves K extra coins from the payer to "Bank". "rule fee off" stops this. A new "rule fee K" replaces the old K.
- "rule cap K" : from now on a payment of more than K coins is rejected (nothing moves, no fee). "rule cap off" lifts it.
- "rule freeze X" : from now on any payment TO the account named X (under any of its names) is rejected (nothing moves, no fee). "rule freeze off" lifts it, a new "rule freeze Y" replaces it. If both rules would reject a payment, it is rejected by freeze.
- A payment rejected by a rule still counts as an event that "fired" that rule. Every payment charged a fee counts as an event that fired the fee rule (a later undo does not change that).

LOG
"""
_QUESTIONS = """
QUESTIONS (answer from the log above; the log is the only source)
Q1. What is the final balance of the account known as {q1}? (a whole number)
Q2. Who holds the {item} at the end? (give a name)
Q3. What is the number of the first event that fired the {rule} rule? (the event number as shown)
Q4. How many events fired the fee rule in total? (a whole number)
Q5. Which account has the highest final balance? (give a name)

Reply with exactly five lines and nothing else, in this form:
Q1: <answer>
Q2: <answer>
Q3: <answer>
Q4: <answer>
Q5: <answer>
"""


# ------------------------------------------------------------------ generator
def _rng(seed: int, attempt: int) -> random.Random:
    h = hashlib.sha256(f"ledger-gen:{seed}:{attempt}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def _names(rng: random.Random, n: int) -> list[str]:
    cons, vow = "bdfgklmnprstvz", "aeiou"
    seen, out = set(), []
    while len(out) < n:
        w = rng.choice(cons) + rng.choice(vow) + rng.choice(cons) + rng.choice(vow)
        if w not in seen:
            seen.add(w)
            out.append(w.capitalize())
    return out


def _build(seed: int, level: int, attempt: int) -> dict | None:
    cfg = LEVELS[level]
    rng = _rng(seed, attempt)
    n_acc = cfg["core"] + cfg["distract"]
    pool = _names(rng, n_acc + cfg["aliases"] + 2)
    accts = list(range(n_acc))
    base = {i: pool[i] for i in accts}
    alias_pool = pool[n_acc:]
    core = accts[:cfg["core"]]
    items = _ITEMS[:3 + level]
    holder = {it: rng.choice(core) for it in items}
    opening = ", ".join(f"{it} with {base[holder[it]]}" for it in items)
    names = {i: [base[i]] for i in accts}
    n = cfg["events"]
    sched = {}
    for frac, rule, arg in _SCHED[level]:
        sched[max(5, int(n * frac))] = (rule, arg)
    alias_at = set(rng.sample(range(8, int(n * 0.8)), cfg["aliases"]))
    hubs = rng.sample(core, 2)
    evs: list[dict] = []
    txs: list[int] = []
    undone: set[int] = set()

    def nm(i):
        return rng.choice(names[i])
    for e in range(1, n + 1):
        if e in sched:
            rule, arg = sched[e]
            if arg == "@hub":
                arg = hubs[0]
            elif arg == "@hub2":
                arg = hubs[1]
            ev = {"k": "rule", "rule": rule, "arg": arg}
            if rule == "freeze" and arg is not None:
                ev["argn"] = nm(arg)
            evs.append(ev)
            continue
        if e in alias_at and alias_pool:
            who = rng.choice(core)
            alias = alias_pool.pop()
            evs.append({"k": "alias", "who": who, "an": nm(who), "name": alias})
            names[who].append(alias)
            continue
        r = rng.random()
        cand = [t for t in txs[-120:] if t not in undone]
        if r < cfg["undo"] and cand:
            t = rng.choice(cand)
            undone.add(t)
            evs.append({"k": "undo", "t": t})
        elif r < cfg["undo"] + 0.07:
            it = rng.choice(items)
            a = holder[it]
            b = rng.choice([x for x in core if x != a])
            evs.append({"k": "item", "item": it, "a": a, "b": b, "an": nm(a), "bn": nm(b)})
            holder[it] = b
        else:
            a = rng.choice(accts)
            b = rng.choice(core) if rng.random() < 0.45 else rng.choice(accts)
            if b == a:
                b = (a + 1) % n_acc
            evs.append({"k": "tx", "a": a, "b": b, "an": nm(a), "bn": nm(b),
                        "amt": rng.randrange(10, 500)})
            txs.append(e)
    return {"cfg": cfg, "base": base, "names": names, "items": items, "events": evs,
            "opening": opening, "core": core}


# ------------------------------------------------------------------ simulator
def simulate(inst: dict, *, ignore_undo=False, alias_distinct=False, ignore_rules=False,
             skip=(), amt_override=None, undo_reapplies=False, no_fee=False,
             ignore_undo_of=()) -> dict:
    """Reference simulator. The flags are plausible slips (mutants) or counterfactuals:
    skip = event numbers that become no-ops (counterfactual "line N is not in the log"),
    amt_override = {event: amount}, undo_reapplies = the pre-4.5 bug (an undo applied the
    payment a second time), no_fee = the fee rule never charges, ignore_undo_of = undo
    lines aimed at these events do nothing."""
    base = inst["base"]
    amt_override = amt_override or {}
    bal = {i: START for i in base}
    bal["Bank"] = 0
    key_of = {nm: i for i, nm in base.items()}        # name -> account key
    if alias_distinct:                                 # alias treated as a new account
        key_of = {nm: nm for nm in base.values()}
        bal = {nm: START for nm in base.values()}
        bal["Bank"] = 0
    hold = {}
    for it_line in inst["opening"].split(", "):
        it, who = it_line.split(" with ")
        hold[it] = key_of[who]
    fee = cap = freeze = None
    fired = {"fee": [], "cap": [], "freeze": []}
    rejected: list[tuple] = []          # (event, payer key, payee key, rule)
    state: dict[int, tuple] = {}        # event -> (fee, cap, freeze) in force when a payment ran
    deltas: dict[int, list] = {}
    for e, ev in enumerate(inst["events"], 1):
        k = ev["k"]
        if e in skip:
            continue

        def key(i, name):
            if alias_distinct:
                if name not in key_of:
                    key_of[name] = name
                    bal[name] = START
                return key_of[name]
            return i
        if k == "rule":
            if ignore_rules:
                continue
            r, a = ev["rule"], ev["arg"]
            if r == "fee":
                fee = a
            elif r == "cap":
                cap = a
            else:
                freeze = None if a is None else key(a, ev.get("argn"))
        elif k == "alias":
            if not alias_distinct:
                key_of[ev["name"]] = ev["who"]
        elif k == "item":
            hold[ev["item"]] = key(ev["b"], ev["bn"])
        elif k == "undo":
            if ignore_undo or ev["t"] in ignore_undo_of:
                continue
            for acct, d in deltas.pop(ev["t"], []):
                bal[acct] += d if undo_reapplies else -d
        else:
            a, b = key(ev["a"], ev["an"]), key(ev["b"], ev["bn"])
            amt = amt_override.get(e, ev["amt"])
            fee_now = None if no_fee else fee
            state[e] = (fee_now, cap, freeze)
            if freeze is not None and b == freeze:
                fired["freeze"].append(e)
                rejected.append((e, a, b, "freeze"))
            elif cap is not None and amt > cap:
                fired["cap"].append(e)
                rejected.append((e, a, b, "cap"))
            else:
                d = [(a, -amt), (b, amt)]
                if fee_now:
                    d += [(a, -fee_now), ("Bank", fee_now)]
                    fired["fee"].append(e)
                for acct, x in d:
                    bal[acct] += x
                deltas[e] = d
    return {"bal": bal, "hold": hold, "fired": fired, "rejected": rejected, "state": state}


def _answers(inst: dict, sim: dict, q: dict, *, distinct=False) -> list[str]:
    """Five answer strings computed from a simulation, for the questions q."""
    bal, hold = sim["bal"], sim["hold"]
    base = inst["base"]
    k1 = q["q1_name"] if distinct else q["q1_acct"]
    accts = {a: v for a, v in bal.items()}
    top = max(accts, key=lambda a: accts[a])
    top_name = top if distinct else (base[top] if top in base else "Bank")
    h = hold[q["item"]]
    h_name = h if distinct else base[h]
    first = sim["fired"][q["rule"]]
    return [str(bal.get(k1, START)), h_name, f"{first[0]:04d}" if first else "0000",
            str(len(sim["fired"]["fee"])), top_name]


def _make(seed: int, level: int) -> dict:
    for attempt in range(200):
        inst = _build(seed, level, attempt)
        sim = simulate(inst)
        f = sim["fired"]
        if len(f["fee"]) < 3:
            continue
        rules = [r for r in ("freeze", "cap") if r in {x[1] for x in _SCHED[level]}]
        rule = next((r for r in rules if f[r]), None) if rules else "fee"
        if rules and (rule is None or any(not f[r] for r in rules)):
            continue
        if not rules:
            rule = "fee"
        rng = _rng(seed, 10_000 + attempt)
        core = inst["core"]
        counts = {c: 0 for c in core}
        for ev in inst["events"]:
            for fld in ("a", "b"):
                if ev.get(fld) in counts:
                    counts[ev[fld]] += 1
        acct = max(core, key=lambda c: (counts[c], -c))
        if counts[acct] < 5:
            continue
        aliased = [nm for nm in inst["names"][acct][1:]]
        q1_name = rng.choice(aliased) if aliased else inst["base"][acct]
        top = sorted(sim["bal"].values(), reverse=True)
        if top[0] == top[1]:
            continue
        item = rng.choice(inst["items"])
        q = {"q1_acct": acct, "q1_name": q1_name, "item": item, "rule": rule}
        ans = _answers(inst, sim, q)
        if len(set(ans)) < 5 or ans[3] == ans[2].lstrip("0"):
            continue
        inst["q"], inst["answers"] = q, ans
        return inst
    raise RuntimeError("ledger: no acceptable instance")


# ------------------------------------------------------------------ v2 (levels 2-4)
# Bounded questions about a long log: every question names the accounts it needs (12-25
# touching events each, found among confusable names and aliases) and asks something a
# replay-by-habit gets wrong (a counterfactual, an inverse, an undo of a fee-charged
# payment). Levels 1 and 5 keep the v1 global questions.
V2_LEVELS = (2, 3, 4, 5)       # L5 = the round-2 knob (3,000 events)
_PAIRS = {2: 1, 3: 2, 4: 3, 5: 3}
_KINDS = {2: ["BAL", "HOLD", "FIRST", "CF", "BAL2"],
          3: ["BAL", "HOLD", "FIRST", "CF", "INV"],
          4: ["BAL", "HOLD", "FIRST", "CF", "INV"],
          5: ["BAL", "HOLD", "FIRST", "CF", "INV"]}
_NUM_KIND = {"BAL": True, "BAL2": True, "HOLD": False, "FIRST": True, "CF": True, "INV": True}

_PREAMBLE2_EXTRA = """- Some accounts have names that differ by one letter. They are different accounts.
- Every question depends only on the accounts and items it names. You do not need to track the other accounts.
"""
_Q2 = {
    "BAL": "What is the final balance of the account known as {name}? (a whole number)",
    "BAL2": "What is the final balance of the account known as {name}? (a whole number)",
    "HOLD": "Who holds the {item} at the end? (give a name)",
    "FIRST": ("What is the number of the first event in which a payment to the account known as {name} "
              "was rejected by the {rule} rule? (the event number as shown)"),
    "CF": ("Suppose line {line:04d} ({text}) had never been written: that line does nothing, and every other "
           "line stays exactly as it is, with the same event numbers and the same undo lines. What would the "
           "final balance of the account known as {name} be? (a whole number)"),
    "INV": ("Line {line:04d} reads \"{text}\": its amount is smudged and unreadable. The final balance of the "
            "account known as {name} was {value}. What was the amount? (a whole number from 10 to 499)"),
}


class _Retry(Exception):
    pass


def _twin(rng: random.Random, name: str, taken: set) -> str:
    cons, vow = "bdfgklmnprstvz", "aeiou"
    for _ in range(200):
        i = rng.randrange(len(name))
        pool = vow if name[i].lower() in vow else cons
        c = rng.choice(pool)
        w = name[:i] + (c.upper() if i == 0 else c) + name[i + 1:]
        if w != name and w not in taken:
            return w
    raise _Retry()


def _build2(seed: int, level: int, attempt: int) -> dict | None:
    try:
        return _build2_inner(seed, level, attempt)
    except _Retry:
        return None


def _build2_inner(seed: int, level: int, attempt: int) -> dict:
    cfg = LEVELS[level]
    rng = _rng(seed, 5000 + attempt)
    n, ncore = cfg["events"], cfg["core"]
    nacc = ncore + cfg["distract"]
    T1, T2, T3 = 0, 1, 2
    pool = _names(rng, nacc + cfg["aliases"] + 2)
    base = {i: pool[i] for i in range(nacc)}
    alias_pool = pool[nacc:]
    taken = set(pool)
    twin = {}
    for p, v in enumerate((T1, T3, T2)[:_PAIRS[level]]):
        base[ncore + p] = _twin(rng, base[v], taken)
        taken.add(base[ncore + p])
        twin[v] = ncore + p
    others = list(range(3, ncore))
    nontarget = others + list(range(ncore, nacc))
    twins = list(twin.values())
    hubs = rng.sample(others, 2)
    items = _ITEMS[:3 + level]
    qitem = rng.choice(items)
    holder = {it: rng.choice(others) for it in items}
    opening = ", ".join(f"{it} with {base[holder[it]]}" for it in items)

    sched = {}
    for frac, rule, arg in _SCHED[level]:
        sched[max(5, int(n * frac))] = (rule, arg)
    rule_pos = sorted(sched.items())

    def state_at(e):
        st = {"fee": None, "cap": None, "freeze": None}
        for p, (rule, arg) in rule_pos:
            if p >= e:
                break
            if rule == "freeze":
                st["freeze"] = None if arg is None else hubs[0 if arg == "@hub" else 1]
            else:
                st[rule] = arg
        return st

    free = [p for p in range(8, n + 1) if p not in sched]
    free_set = set(free)

    def take(pred=lambda p: True, lo=8, hi=n):
        cands = [p for p in free if lo <= p <= hi and p in free_set and pred(p)]
        if not cands:
            raise _Retry()
        p = rng.choice(cands)
        free_set.discard(p)
        return p

    plan: dict[int, dict] = {}
    alias_plan: dict[int, dict] = {}

    def cp(excl=()):
        return rng.choice([x for x in nontarget if x not in excl])

    def cpf(p):
        """a counterpart that is not the account frozen at event p"""
        fz = state_at(p)["freeze"]
        return cp(excl=(fz,) if fz is not None else ())

    def put_tx(p, a, b, amt, **kw):
        plan[p] = {"k": "tx", "a": a, "b": b, "amt": amt, **kw}

    def plain(t, count):
        for _ in range(count):
            p = take()
            c = cp()
            a, b = (t, c) if rng.random() < 0.5 else (c, t)
            put_tx(p, a, b, rng.randrange(10, 500))

    def fee_undo(t):
        """t pays an executed, fee-charged payment, and a later undo reverses it"""
        p = take(lambda p: state_at(p)["fee"] and p < n - 40)
        st = state_at(p)
        put_tx(p, t, cpf(p), rng.randrange(10, (st["cap"] or 499) + 1))
        q = take(lambda x: x > p)
        plan[q] = {"k": "undo", "t": p}

    def cap_reject(t, pred=lambda p: True):
        p = take(lambda p: state_at(p)["cap"] is not None and pred(p))
        c = cpf(p)
        a, b = (t, c) if rng.random() < 0.5 else (c, t)
        put_tx(p, a, b, rng.randrange(state_at(p)["cap"] + 10, 500))

    # ---- T1: balance asked by an alias, two rejections, an undone fee-charged payment
    plain(T1, rng.randrange(9, 12))
    fee_undo(T1)
    if level >= 3:
        p = take(lambda p: state_at(p)["freeze"] is not None)
        put_tx(p, T1, state_at(p)["freeze"], rng.randrange(10, 400))
        cap_reject(T1, lambda p: state_at(p)["freeze"] is None)
    else:
        cap_reject(T1)
        cap_reject(T1)
    # ---- T2: counterfactual target. The first cap line rejects two of its payments; one is undone later
    first_cap = next(p for p, (r, a) in rule_pos if r == "cap")
    k1 = sched[first_cap][1]
    nxt = next((p for p, (r, a) in rule_pos if r == "cap" and p > first_cap), n + 1)

    def in_win(p):
        return first_cap < p < nxt
    p_und = take(lambda p: in_win(p) and state_at(p)["fee"] and p < n - 40)
    c = cpf(p_und)
    a, b = (T2, c) if rng.random() < 0.5 else (c, T2)
    put_tx(p_und, a, b, rng.randrange(k1 + 10, 500))
    plan[take(lambda x: x > p_und)] = {"k": "undo", "t": p_und}
    p2 = take(in_win)
    c = cpf(p2)
    a, b = (T2, c) if rng.random() < 0.5 else (c, T2)
    put_tx(p2, a, b, rng.randrange(k1 + 10, 500))
    plain(T2, rng.randrange(9, 12))
    # ---- T3: the smudged payment (inverse) or a second balance question
    plain(T3, rng.randrange(11, 15))
    smudge = None
    if level >= 3:
        smudge = take(lambda p: state_at(p)["fee"])
        st = state_at(smudge)
        put_tx(smudge, T3, cpf(smudge), rng.randrange(10, (st["cap"] or 499) + 1), smudge=True)
    if level >= 4:
        fee_undo(T3)
    # ---- FIRST: two payments to the named account, rejected by the named rule
    if level >= 3:
        x_acct, x_rule = hubs[0], "freeze"
        for _ in range(2):
            p = take(lambda p: state_at(p)["freeze"] == hubs[0])
            put_tx(p, cp(excl=(hubs[0],)), hubs[0], rng.randrange(10, 400))
    else:
        x_acct, x_rule = rng.choice(others), "cap"
        for _ in range(2):
            p = take(lambda p: state_at(p)["cap"] is not None)
            put_tx(p, cp(excl=(x_acct,)), x_acct, rng.randrange(state_at(p)["cap"] + 10, 500))
    # ---- HOLD: a hand-over chain through a confusable twin, ending at an aliased account
    h0 = holder[qitem]
    b_acct = rng.choice([x for x in others if x != h0])
    f_acct = rng.choice([x for x in others if x != b_acct])
    chain_pos = sorted(take() for _ in range(3))
    for p, (a, b) in zip(chain_pos, [(h0, twin[T1]), (twin[T1], b_acct), (b_acct, f_acct)]):
        plan[p] = {"k": "item", "item": qitem, "a": a, "b": b}
    plan[chain_pos[2]]["prefer_alias"] = True
    # ---- aliases
    want = [(T1, 8, int(n * 0.25), None)]
    if level >= 3:
        want.append((T1, int(n * 0.25) + 1, int(n * 0.5), "chain"))
    want.append((T2, 8, int(n * 0.8), None))
    if level >= 3:
        want.append((T3, 8, int(n * 0.8), None))
        fz = next(p for p, (r, a) in rule_pos if r == "freeze")
        want.append((hubs[0], 8, fz - 1, None))
    want.append((f_acct, 8, chain_pos[2] - 1, None))
    extra = cfg["aliases"] - len(want)
    if extra < 0:
        raise _Retry()
    want += [(rng.choice(others), 8, int(n * 0.8), None) for _ in range(extra)]
    for who, lo, hi, tag in want:
        alias_plan[take(lo=lo, hi=hi)] = {"who": who, "tag": tag}

    # ---- forward pass (names are resolved in time order)
    names = {i: [base[i]] for i in base}
    evs: list[dict] = []
    undone: set[int] = set()
    bg_txs: list[int] = []
    last_alias: dict[int, str] = {}
    weights = [2.5 if x in twins else (2.0 if x in others else 1.0) for x in nontarget]
    bg_items = [x for x in items if x != qitem]
    bg_holder = {it: holder[it] for it in bg_items}

    def nm(i, prefer_alias=False):
        if prefer_alias and len(names[i]) > 1:
            return names[i][-1]
        return rng.choice(names[i])

    def wacct():
        return rng.choices(nontarget, weights)[0]
    for e in range(1, n + 1):
        if e in sched:
            rule, arg = sched[e]
            arg = hubs[0] if arg == "@hub" else (hubs[1] if arg == "@hub2" else arg)
            ev = {"k": "rule", "rule": rule, "arg": arg}
            if rule == "freeze" and arg is not None:
                ev["argn"] = last_alias[arg] if arg == hubs[0] and arg in last_alias else nm(arg)
            evs.append(ev)
        elif e in alias_plan:
            who, tag = alias_plan[e]["who"], alias_plan[e]["tag"]
            an = last_alias[who] if tag == "chain" and who in last_alias else nm(who)
            alias = alias_pool.pop()
            evs.append({"k": "alias", "who": who, "an": an, "name": alias})
            names[who].append(alias)
            last_alias[who] = alias
        elif e in plan:
            sp = dict(plan[e])
            if sp["k"] == "tx":
                sp.update(an=nm(sp["a"]), bn=nm(sp["b"]))
            elif sp["k"] == "item":
                sp.update(an=nm(sp["a"]), bn=nm(sp["b"], sp.pop("prefer_alias", False)))
            evs.append(sp)
        else:
            r = rng.random()
            cand = [t for t in bg_txs[-120:] if t not in undone]
            if r < cfg["undo"] and cand:
                t = rng.choice(cand)
                undone.add(t)
                evs.append({"k": "undo", "t": t})
            elif r < cfg["undo"] + 0.07:
                it = rng.choice(bg_items)
                a = bg_holder[it]
                b = rng.choice([x for x in others + twins if x != a])
                evs.append({"k": "item", "item": it, "a": a, "b": b, "an": nm(a), "bn": nm(b)})
                bg_holder[it] = b
            else:
                a = wacct()
                b = rng.choice(others) if rng.random() < 0.45 else wacct()
                if b == a:
                    b = others[0] if a != others[0] else others[1]
                evs.append({"k": "tx", "a": a, "b": b, "an": nm(a), "bn": nm(b), "amt": rng.randrange(10, 500)})
                bg_txs.append(e)
    return {"cfg": cfg, "base": base, "names": names, "items": items, "events": evs, "opening": opening,
            "core": list(range(ncore)), "v2": True, "targets": (T1, T2, T3), "twin": twin, "hubs": hubs,
            "smudge": smudge, "first_cap": first_cap, "cap_window": (first_cap, nxt),
            "x": (x_acct, x_rule), "qitem": qitem, "last_alias": last_alias, "f": f_acct}


def _touching(inst: dict, acct: int) -> int:
    evs = inst["events"]
    n = 0
    for ev in evs:
        if ev["k"] == "tx" and acct in (ev["a"], ev["b"]):
            n += 1
        elif ev["k"] == "undo":
            t = evs[ev["t"] - 1]
            if t["k"] == "tx" and acct in (t["a"], t["b"]):
                n += 1
    return n


def _inv_candidates(inst: dict, key, **fl) -> list[int]:
    """Amounts 10..499 for the smudged line that reproduce the stated final balance of ``key``.
    One simulation with amount 10, then arithmetic: the line moves nothing when the cap in force
    rejects it, otherwise it takes the amount (+ the fee) off the payer; nothing else depends on it."""
    s, ev, q = inst["smudge"], inst["events"][inst["smudge"] - 1], inst["q"]
    sim = simulate(inst, amt_override={s: 10}, **fl)
    fee, cap, _frz = sim["state"][s]
    bal10 = sim["bal"][key]
    if any(r[0] == s for r in sim["rejected"]):            # rejected by freeze: the amount cannot matter
        return list(range(10, 500)) if bal10 == q["inv_value"] else []
    payer = ev["an"] if fl.get("alias_distinct") else ev["a"]
    out = []
    for a in range(10, 500):
        if payer != key:
            val = bal10
        elif cap is not None and a > cap:
            val = bal10 + 10 + (fee or 0)
        else:
            val = bal10 + 10 - a
        if val == q["inv_value"]:
            out.append(a)
    return out


def _answers2(inst: dict, **fl) -> list[str]:
    q = inst["q"]
    distinct = fl.get("alias_distinct", False)
    sim = simulate(inst, **fl)
    base = inst["base"]
    out = []
    for kind in q["kinds"]:
        if kind in ("BAL", "BAL2"):
            qq = q[kind]
            out.append(str(sim["bal"].get(qq["name"] if distinct else qq["acct"], START)))
        elif kind == "HOLD":
            h = sim["hold"][q["item"]]
            out.append(h if distinct else base[h])
        elif kind == "FIRST":
            x = q["FIRST"]
            hit = [r[0] for r in sim["rejected"] if r[3] == x["rule"] and r[2] == (x["name"] if distinct else x["acct"])]
            out.append(f"{hit[0]:04d}" if hit else "0000")
        elif kind == "CF":
            qq = q["CF"]
            s2 = simulate(inst, skip={qq["line"]}, **fl)
            out.append(str(s2["bal"].get(qq["name"] if distinct else qq["acct"], START)))
        else:
            qq = q["INV"]
            sols = _inv_candidates(inst, qq["name"] if distinct else qq["acct"], **fl)
            out.append(str(sols[0]) if sols else "0")
    return out


def _make2(seed: int, level: int) -> dict:
    kinds = _KINDS[level]
    for attempt in range(400):
        inst = _build2(seed, level, attempt)
        if inst is None:
            continue
        t1, t2, t3 = inst["targets"]
        evs, base, names = inst["events"], inst["base"], inst["names"]
        if any(not 12 <= _touching(inst, t) <= 25 for t in inst["targets"]):
            continue
        sim = simulate(inst)
        rej = sim["rejected"]
        rej_set = {r[0] for r in rej}
        undone = {u["t"] for u in evs if u["k"] == "undo"}

        def mine(t):
            return [e for e, ev in enumerate(evs, 1) if ev["k"] == "tx" and t in (ev["a"], ev["b"])]
        if sum(1 for r in rej if t1 in (r[1], r[2])) < 2:
            continue
        if not [e for e in mine(t1) if e in undone and e not in rej_set and sim["state"][e][0]]:
            continue
        if any(len(mine(x)) < 5 for x in inst["twin"].values()):
            continue
        c1, nxt = inst["cap_window"]
        win = {r[0] for r in rej if t2 in (r[1], r[2]) and r[3] == "cap" and c1 < r[0] < nxt}
        cf = simulate(inst, skip={c1})["bal"][t2]
        naive = simulate(inst, skip={c1}, ignore_undo_of=win)["bal"][t2]
        if len(win) < 2 or not win & undone or cf == sim["bal"][t2] or cf == naive:
            continue
        if sim["bal"][inst["twin"][t1]] == sim["bal"][t1]:
            continue
        xa, xr = inst["x"]
        if sum(1 for r in rej if r[2] == xa and r[3] == xr) < 2:
            continue
        la = inst["last_alias"]
        rng = _rng(seed, 10_000 + attempt)
        # ask about X by a name other than the one the freeze line used
        fname = next((ev.get("argn") for ev in evs if ev["k"] == "rule" and ev["rule"] == "freeze"
                      and ev["arg"] == xa), None)
        xnames = [x for x in names[xa] if x != fname] or names[xa]
        q = {"kinds": kinds, "item": inst["qitem"], "rule": xr, "inv_value": None}
        q["BAL"] = {"acct": t1, "name": la[t1]}
        q["BAL2"] = {"acct": t3, "name": la.get(t3, base[t3])}
        q["FIRST"] = {"acct": xa, "name": rng.choice(xnames), "rule": xr}
        q["CF"] = {"acct": t2, "name": la[t2], "line": c1}
        inst["q"] = q
        if inst["smudge"]:
            s = inst["smudge"]
            q["inv_value"] = sim["bal"][t3]
            q["INV"] = {"acct": t3, "name": la.get(t3, base[t3]), "line": s, "value": sim["bal"][t3]}
            if _inv_candidates(inst, t3) != [evs[s - 1]["amt"]]:
                continue
        ans = _answers2(inst)
        if len(set(ans)) < 5:
            continue
        inst["answers"] = ans
        return inst
    raise RuntimeError("ledger v2: no acceptable instance")


def _seed(gen: dict, release_seed: str, case_id: str) -> tuple[int, int]:
    level = int(gen["level"])
    if level not in LEVELS:
        raise ValueError(f"ledger: unknown level {level}")
    return _gen.instance_int(FAMILY, release_seed, case_id, level, gen.get("key")), level


def _render_one(ev: dict, e: int) -> str:
    k = ev["k"]
    if k == "tx":
        s = f"{ev['an']}>{ev['bn']} " + ("??" if ev.get("smudge") else str(ev["amt"]))
    elif k == "undo":
        s = f"undo {ev['t']:04d}"
    elif k == "alias":
        s = f"{ev['an']}={ev['name']}"
    elif k == "item":
        s = f"item {ev['item']} {ev['an']}>{ev['bn']}"
    else:
        arg = "off" if ev["arg"] is None else (ev["argn"] if ev["rule"] == "freeze" else ev["arg"])
        s = f"rule {ev['rule']} {arg}"
    return f"{e:04d} {s}"


def _render(inst: dict) -> str:
    return "\n".join(_render_one(ev, e) for e, ev in enumerate(inst["events"], 1))


_NUMERIC = (True, False, True, True, False)      # Q1..Q5 answer kinds


def _accept(inst: dict) -> list[list[str]]:
    """Accepted spellings per question (lower-case names, plain numbers)."""
    ans, q = inst["answers"], inst["q"]
    top_acct = max(inst["_sim"]["bal"], key=lambda a: inst["_sim"]["bal"][a])
    h = inst["_sim"]["hold"][q["item"]]
    names_top = ["bank"] if top_acct == "Bank" else [x.lower() for x in inst["names"][top_acct]]
    return [[ans[0]], [x.lower() for x in inst["names"][h]], [ans[2]], [ans[3]], names_top]


def _num_forms(v: str) -> list[str]:
    n = int(v)
    return sorted({str(n), f"{n:,}"})


def _checks(accept: list[list[str]], numeric=_NUMERIC) -> list[dict]:
    out = []
    for i, acc in enumerate(accept, 1):
        if numeric[i - 1]:
            forms = []
            for v in acc:
                for f in _num_forms(v):
                    neg = f.startswith("-")
                    forms.append(("-" if neg else "") + "0*" + re.escape(f.lstrip("-")))
            alt = "|".join(forms)
        else:
            alt = "|".join(re.escape(v) for v in acc)
        out.append({"id": f"Q{i}", "type": "regex",
                    "last_of": rf"(?i)^[\s*>\-]*Q{i}[\s*]*[:=]",
                    "pattern": rf"(?i)^[\s*>\-]*Q{i}[\s*]*[:=][\s*]*(?:{alt})[\s*.]*$"})
    return out


def _instance(gen: dict, release_seed: str, case_id: str) -> dict:
    seed, level = _seed(gen, release_seed, case_id)
    inst = _make2(seed, level) if level in V2_LEVELS else _make(seed, level)
    inst["_sim"] = simulate(inst)
    inst["level"] = level
    return inst


def _accept2(inst: dict) -> list[list[str]]:
    q = inst["q"]
    out = []
    for i, kind in enumerate(q["kinds"]):
        if kind == "HOLD":
            out.append([x.lower() for x in inst["names"][inst["_sim"]["hold"][q["item"]]]])
        else:
            out.append([inst["answers"][i]])
    return out


def _render_q2(inst: dict) -> str:
    q = inst["q"]
    lines = []
    for i, kind in enumerate(q["kinds"], 1):
        if kind == "HOLD":
            t = _Q2[kind].format(item=q["item"])
        elif kind == "FIRST":
            t = _Q2[kind].format(name=q["FIRST"]["name"], rule=q["FIRST"]["rule"])
        elif kind == "CF":
            c = q["CF"]
            t = _Q2[kind].format(line=c["line"], name=c["name"], text=_render_one(inst["events"][c["line"] - 1],
                                                                                   c["line"])[5:])
        elif kind == "INV":
            c = q["INV"]
            t = _Q2[kind].format(line=c["line"], name=c["name"], value=c["value"],
                                 text=_render_one(inst["events"][c["line"] - 1], c["line"])[5:])
        else:
            t = _Q2[kind].format(name=q[kind]["name"])
        lines.append(f"Q{i}. {t}")
    tail = "\n".join(f"Q{i}: <answer>" for i in range(1, 6))
    return ("\nQUESTIONS (answer from the log above; the log is the only source)\n" + "\n".join(lines)
            + "\n\nReply with exactly five lines and nothing else, in this form:\n" + tail + "\n")


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    inst = _instance(gen, release_seed, case_id)
    if inst.get("v2"):
        pre = _PREAMBLE.replace("\n\nLOG\n", "\n" + _PREAMBLE2_EXTRA + "\nLOG\n")
        prompt = pre.format(opening=inst["opening"]) + _render(inst) + "\n" + _render_q2(inst)
        accept = _accept2(inst)
        numeric = [_NUM_KIND[k] for k in inst["q"]["kinds"]]
        q = inst["q"]
        return {"prompt": prompt, "temperature": 0.0, "max_tokens": 6144,
                "grader": "checks", "grader_config": {"checks": _checks(accept, numeric)},
                "accept": accept, "numeric": numeric,
                "meta": {"level": inst["level"], "events": len(inst["events"]), "v2": True,
                         "accounts": len(inst["base"]), "prompt_chars": len(prompt),
                         "prompt_tokens_est": round(len(prompt) / 4), "answers": inst["answers"],
                         "kinds": q["kinds"], "rule": q["rule"], "cf_line": q["CF"]["line"],
                         "inv_line": inst["smudge"], "inv_value": q["inv_value"],
                         "touching": [_touching(inst, t) for t in inst["targets"]]}}
    q = inst["q"]
    prompt = (_PREAMBLE.format(opening=inst["opening"]) + _render(inst) + "\n"
              + _QUESTIONS.format(q1=q["q1_name"], item=q["item"], rule=q["rule"]))
    accept = _accept(inst)
    return {"prompt": prompt, "temperature": 0.0, "max_tokens": 512,
            "grader": "checks", "grader_config": {"checks": _checks(accept)},
            "accept": accept,
            "meta": {"level": inst["level"], "events": len(inst["events"]),
                     "accounts": len(inst["base"]), "prompt_chars": len(prompt),
                     "prompt_tokens_est": round(len(prompt) / 4), "answers": inst["answers"],
                     "rule": q["rule"]}}


def _fmt(ans: list[str]) -> str:
    return "\n".join(f"Q{i}: {a}" for i, a in enumerate(ans, 1))


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return _fmt(_instance(gen, release_seed, case_id)["answers"])


def grade_per_question(answer: str, accept: list[list[str]], numeric=_NUMERIC) -> list[bool]:
    res = []
    lines = answer.splitlines()
    for i, acc in enumerate(accept, 1):
        hit = [ln for ln in lines if re.match(rf"(?i)^[\s*>\-]*Q{i}[\s*]*[:=]", ln)]
        if not hit:
            res.append(False)
            continue
        val = re.sub(rf"(?i)^[\s*>\-]*Q{i}[\s*]*[:=][\s*]*", "", hit[-1]).rstrip("*. \t\r")
        if numeric[i - 1]:
            neg = val.startswith("-")
            core = val.lstrip("-")
            core = core.lstrip("0") or ("0" if core and set(core) == {"0"} else core)
            ok = any(core in {x.lstrip("-") for x in _num_forms(v)} and neg == v.startswith("-")
                     for v in acc)
        else:
            ok = val.lower() in acc
        res.append(ok)
    return res


def grade(answer: str, materialized: dict) -> float:
    from .. import graders          # the engine strips thinking tags before its checks: so does this
    r = grade_per_question(graders.strip_thinking_tags(answer), materialized["accept"],
                           materialized.get("numeric", _NUMERIC))
    return sum(r) / len(r)


def mutants_named(gen: dict, *, release_seed: str, case_id: str) -> list[tuple[str, str]]:
    """(name, answer text) pairs, each a plausible wrong answer set that differs from the reference."""
    inst = _instance(gen, release_seed, case_id)
    q = inst["q"]
    if inst.get("v2"):
        accept, numeric = _accept2(inst), [_NUM_KIND[k] for k in q["kinds"]]
        ans, kinds = inst["answers"], q["kinds"]
        sim = inst["_sim"]
        cand: list[tuple[str, list[str]]] = []
        for nm_, kw, d in (("undo-applies-again", {"undo_reapplies": True}, False),
                           ("ignore-undo", {"ignore_undo": True}, False),
                           ("ignore-rules", {"ignore_rules": True}, False),
                           ("alias-distinct", {"alias_distinct": True}, True)):
            try:
                cand.append((nm_, _answers2(inst, **kw)))
            except (KeyError, IndexError, ValueError):
                pass
        a = list(ans)                                       # CF answered with the factual balance
        a[kinds.index("CF")] = str(sim["bal"][q["CF"]["acct"]])
        cand.append(("factual-for-CF", a))
        a = list(ans)                                       # BAL answered for the confusable twin
        a[kinds.index("BAL")] = str(sim["bal"][inst["twin"][q["BAL"]["acct"]]])
        cand.append(("confusable-swap", a))
        if "INV" in kinds:                                  # INV forgetting the fee that line paid
            fee = sim["state"][inst["smudge"]][0] or 0
            a = list(ans)
            a[kinds.index("INV")] = str(int(ans[kinds.index("INV")]) + fee)
            cand.append(("inv-ignores-fee", a))
        fmt = _fmt
    else:
        accept, numeric = _accept(inst), _NUMERIC
        cand = []
        for nm_, kw, distinct in (("undo-applies-again", {"undo_reapplies": True}, False),
                                  ("ignore-undo", {"ignore_undo": True}, False),
                                  ("ignore-rules", {"ignore_rules": True}, False),
                                  ("alias-distinct", {"alias_distinct": True}, True)):
            try:
                sim = simulate(inst, **kw)
                cand.append((nm_, _answers(inst, sim, q, distinct=distinct)))
            except (KeyError, IndexError, ValueError):
                pass
        ans = inst["answers"]
        cand.append(("balance+1", [str(int(ans[0]) + 1)] + ans[1:]))
        cand.append(("fee-count-1", ans[:3] + [str(int(ans[3]) - 1)] + ans[4:]))
        cand.append(("first-event-1", ans[:2] + [f"{max(1, int(ans[2]) - 1):04d}"] + ans[3:]))
    out = []
    for nm_, a in cand:
        m = _fmt(a)
        if sum(grade_per_question(m, accept, numeric)) < 5:
            out.append((nm_, m))
    return out


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    keep: list[str] = []
    for _n, m in mutants_named(gen, release_seed=release_seed, case_id=case_id):
        if m not in keep:
            keep.append(m)
    return keep


if __name__ == "__main__":
    _m = materialize({"type": FAMILY, "level": int(sys.argv[2]) if len(sys.argv) > 2 else 1},
                     release_seed=sys.argv[1] if len(sys.argv) > 1 else "dev", case_id="LG-demo")
    print(_m["prompt"])
    print("# answers:", json.dumps(_m["meta"]["answers"]), "meta:", json.dumps(_m["meta"]))
