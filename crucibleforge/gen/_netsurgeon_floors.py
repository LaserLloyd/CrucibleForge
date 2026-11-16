"""Helper of gen/netsurgeon.py: the red-team floors of the fast-fail question NETSURGEON (t-work/netsurgeon/redteam.py) as source strings the
engine grader runs: nothing, the ff baseline (SGD on the public probe, ``baseline``), SGD with a held-out third, SGD restarts, "everything is
the new class" (class prior on layer 2), zero layer 2, and weight statistics only. Pinned by ``_FLOORS_SHA`` in netsurgeon.py."""
from . import _netsurgeon_ff as _ff

NOTHING = "def edit(net, probe, spec):\n    return net\n"
CPRIME = '''
def edit(net, probe, spec):
    L2 = net["layers"][1]
    if spec["task"] == "rewrite":
        c = spec["cls"]
    else:
        y = probe["y"]; c = max(set(y), key=y.count)
    L2["W"] = [[0.0]*16 for _ in range(4)]; L2["b"] = [10.0 if k == c else 0.0 for k in range(4)]
    return net
'''
ZERO = '''
def edit(net, probe, spec):
    L2 = net["layers"][1]
    L2["W"] = [[0.0]*16 for _ in range(4)]; L2["b"] = [0.0]*4
    return net
'''
STAT = '''
import math, nn
def edit(net, probe, spec):
    L1, L2 = net["layers"]
    if spec["task"] == "rewrite":
        # bias-only: raise class c' bias until probe key points flip
        c = spec["cls"]
        for _ in range(40):
            P = nn.forward(net, probe["x"])
            bad = [p for p, t in zip(P, probe["tag"]) if t == "key" and max(range(4), key=p.__getitem__) != c]
            if not bad: break
            L2["b"][c] += 0.3
        return net
    if spec["layer"] == 2:       # sort rows by norm (class rows) -- weight statistics only
        rows = sorted(range(4), key=lambda i: sum(w*w for w in L2["W"][i]))
        # nothing principled; keep rows ordered by descending norm
        return net
    norms = [math.sqrt(sum(w*w for w in r)) for r in L1["W"] if any(r)]
    med = sorted(norms)[len(norms)//2]
    for j, r in enumerate(L1["W"]):
        if any(r):
            n = math.sqrt(sum(w*w for w in r)); f = med / n
            if spec["damage"] == "scale":
                L1["W"][j] = [w*f for w in r]; L1["b"][j] *= f
    if spec["damage"] == "flip":   # pick the sign giving higher mean activation on the probe
        for j, r in enumerate(L1["W"]):
            if not any(r): continue
            s = sum(sum(w*v for w, v in zip(r, x)) + L1["b"][j] for x in probe["x"])
            if s < 0:
                L1["W"][j] = [-w for w in r]; L1["b"][j] = -L1["b"][j]
    return net
'''


def _restart():
    r = _ff.BASE.replace("def edit(net, probe, spec):", "def _one(net, probe, spec, seed, lr, budget):", 1)
    r = r.replace("rng = random.Random(0)", "rng = random.Random(seed)").replace("budget = 0.7 * CPU_LIMIT_S", "").replace("lr, mom = 0.1, 0.9", "mom = 0.9")
    return r + '''
import copy, nn
def edit(net, probe, spec):
    best = None
    tot = 0.8 * CPU_LIMIT_S
    for seed, lr in enumerate([0.1, 0.3, 0.03, 0.2]):
        n = _one(copy.deepcopy(net), probe, spec, seed, lr, tot / 4)
        ev = nn.evaluate(n, probe)["loss"]
        if best is None or ev < best[0]:
            best = (ev, n)
    return best[1]
'''


def floors(inst):
    """{name: source}"""
    return {"nothing": NOTHING, "baseline": _ff.baseline(inst), "sgdval": _ff.BASE.replace("USE_VAL = False", "USE_VAL = True"),
            "restarts": _restart(), "cprime": CPRIME, "zero": ZERO, "stat": STAT}
