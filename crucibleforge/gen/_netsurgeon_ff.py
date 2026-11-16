"""(CrucibleForge copy of ff/q/netsurgeon.py @ 11b122c: gen, prompt, reference, baseline, score unchanged; the ff sandbox runner removed; NN scaffold in _netsurgeon_nn.py)
NETSURGEON pocket (tier 1): patch the weights of a tiny frozen 8-16-4 net (pure Python). One task per instance:
REPAIR (a named layer was damaged: rows permuted / signs flipped / rows scaled) or REWRITE (a key region must map to class c').
Graded outside the sandbox on fresh hidden inputs: score = efficacy x specificity. Stdlib only."""
import json, math, pathlib, random
from . import _netsurgeon_nn as nn

NAME, TIERS, CPU_S = "netsurgeon", (1,), 10.0
D, H, C = 8, 16, 4
NN_SRC = (pathlib.Path(__file__).resolve().parent / "_netsurgeon_nn.py").read_text(encoding="utf-8")
KINDS = ["rewrite", "perm", "flip", "scale"]


def _net(W1, b1, W2, b2):
    return {"layers": [{"W": W1, "b": b1, "act": "relu"}, {"W": W2, "b": b2, "act": "softmax"}]}


def _clone(net):
    return json.loads(json.dumps(net))


def _make_net(rng):
    spare = sorted(rng.sample(range(H), 2))
    while True:
        g = [math.exp(rng.gauss(0, 0.6)) for _ in range(H)]
        W1 = [[rng.gauss(0, 0.6) * g[j] for _ in range(D)] for j in range(H)]
        b1 = [rng.gauss(0, 0.3) for _ in range(H)]
        W2 = [[rng.gauss(0, 0.7) for _ in range(H)] for _ in range(C)]
        b2 = [rng.gauss(0, 0.3) for _ in range(C)]
        for s in spare:
            W1[s] = [0.0] * D
            b1[s] = 0.0
            for c in range(C):
                W2[c][s] = 0.0
        net = _net(W1, b1, W2, b2)
        X = [[rng.gauss(0, 1) for _ in range(D)] for _ in range(1500)]
        pr = nn.predict(net, X)
        if all(0.15 <= pr.count(c) / len(pr) <= 0.40 for c in range(C)):
            return net, spare


def _agree(net, ref, X):
    a, b = nn.predict(net, X), nn.predict(ref, X)
    return sum(1 for u, v in zip(a, b) if u == v) / len(X)


def _gx(rng, sd=1.0, shift=0.0):
    return [rng.gauss(0, sd) + (shift if i < 2 else 0) for i in range(D)]


def gen(seed, tier=1):
    rng = random.Random(f"netsurgeon:{seed}")
    kind = KINDS[seed % 4]
    orig, spare = _make_net(rng)
    live = [j for j in range(H) if j not in spare]
    hid = {"orig": orig, "kind": kind, "spare": spare}
    if kind == "rewrite":
        while True:
            i, t = rng.randrange(D), rng.uniform(0.3, 0.9)
            cp = rng.randrange(C)
            Xs = [_gx(rng) for _ in range(1500)]
            reg = [x for x in Xs if x[i] > t]
            if reg and sum(1 for p in nn.predict(orig, reg) if p == cp) / len(reg) < 0.2:
                break

        def pick(n, cond, sd, other_cls):
            out = []
            while len(out) < n:
                x = _gx(rng, sd)
                if cond(x):
                    x = x
                    if other_cls and nn.predict(orig, [x])[0] == cp:
                        continue
                    out.append(x)
            return out
        keyP = []
        while len(keyP) < 6:
            x = [rng.gauss(0, 0.7) for _ in range(D)]
            x[i] = rng.uniform(t, t + 0.6)
            keyP.append(x)
        ctlP = []
        while len(ctlP) < 18:
            x = [rng.gauss(0, 0.7) for _ in range(D)]
            x[i] = rng.uniform(t - 1.2, t - 0.05)
            if nn.predict(orig, [x])[0] != cp:
                ctlP.append(x)
        probe = {"x": keyP + ctlP, "y": [cp] * 6 + nn.predict(orig, ctlP), "tag": ["key"] * 6 + ["ctl"] * 18}
        def near():
            out = []
            while len(out) < 200:
                x = _gx(rng)
                x[i] = rng.uniform(t - 0.3, t)
                if nn.predict(orig, [x])[0] != cp:
                    out.append(x)
            return out
        hid["keysA"] = pick(200, lambda x: x[i] > t, 1.0, False)
        hid["keysB"] = pick(150, lambda x: x[i] > t, 2.0, False)
        hid["ctlA"] = pick(200, lambda x: x[i] <= t, 1.0, True)
        hid["ctlN"] = near()
        hid["ctlB"] = pick(150, lambda x: x[i] <= t, 2.0, True)
        spec = {"task": "rewrite", "feature": i, "threshold": round(t, 3), "cls": cp}
        hid.update(feature=i, thr=round(t, 3), cls=cp, planted_unit=spare[0])
        task = f"REWRITE: every input whose feature {i} is greater than {round(t, 3)} must be classified as class {cp}."
        damaged = None
    else:
        while True:
            net = _clone(orig)
            L1, L2 = net["layers"]
            if kind == "perm":
                while True:
                    p = list(range(C)); rng.shuffle(p)
                    if all(p[c] != c for c in range(C)):
                        break
                L2["W"] = [orig["layers"][1]["W"][p[c]] for c in range(C)]
                L2["b"] = [orig["layers"][1]["b"][p[c]] for c in range(C)]
                hid["fix"] = {"perm": p}
                layer = 2
                dtxt = "permuted: the four rows (one per class) were shuffled, so that every class row now sits at a different position"
            else:
                rows = sorted(rng.sample(live, 5))
                layer = 1
                if kind == "flip":
                    for j in rows:
                        L1["W"][j] = [-w for w in L1["W"][j]]; L1["b"][j] = -L1["b"][j]
                    hid["fix"] = {"rows": rows, "factors": [-1.0] * 5}
                    dtxt = "sign-flipped: five rows of the layer (each row's weights and its bias) were negated"
                else:
                    fac = [rng.choice([0.25, 4.0]) for _ in rows]
                    for j, f in zip(rows, fac):
                        L1["W"][j] = [w * f for w in L1["W"][j]]; L1["b"][j] *= f
                    hid["fix"] = {"rows": rows, "factors": [1 / f for f in fac]}
                    dtxt = "scaled: five rows of the layer (each row's weights and its bias) were multiplied by 0.25 or by 4"
            test = [_gx(rng) for _ in range(600)]
            ad = _agree(net, orig, test)
            if ad <= 0.65:
                break
        pX, py = [], []
        while len(pX) < 24:
            x = _gx(rng, 0.8, 0.5)
            pX.append(x)
            if len(pX) == 24 and any(nn.predict(orig, pX).count(c) < 3 for c in range(C)):
                pX = []
        probe = {"x": pX, "y": nn.predict(orig, pX), "tag": ["all"] * 24}
        hid.update(test=test, agree_damaged=ad, layer=layer)
        spec = {"task": "repair", "layer": layer, "damage": kind}
        task = f"REPAIR: layer {layer} of the net was damaged ({dtxt}). Restore the original behaviour by changing ONLY layer {layer}."
        damaged = net
        hid["damaged"] = net
    net_pub = damaged if damaged else orig
    prompt = PROMPT.replace("@TASK@", task).replace("@CPU@", f"{CPU_S:g}")
    return {"prompt": prompt, "public": {"net": net_pub, "probe": probe, "spec": spec}, "hidden": hid}


def _clean(net):
    try:
        Ls = net["layers"]
        shapes = [(H, D), (C, H)]
        for L, (o, i) in zip(Ls, shapes):
            if len(L["W"]) != o or any(len(r) != i for r in L["W"]) or len(L["b"]) != o:
                return None
        if len(Ls) != 2:
            return None
        out = _net([[float(w) for w in r] for r in Ls[0]["W"]], [float(v) for v in Ls[0]["b"]],
                   [[float(w) for w in r] for r in Ls[1]["W"]], [float(v) for v in Ls[1]["b"]])
        for L in out["layers"]:
            for r in L["W"]:
                if any(not math.isfinite(w) or abs(w) > 1e12 for w in r):
                    return None
            if any(not math.isfinite(w) or abs(w) > 1e12 for w in L["b"]):
                return None
        return out
    except Exception:
        return None


def score(inst, net):
    """-> (q, note). q = efficacy x specificity."""
    h = inst["hidden"]
    orig = h["orig"]
    pub = inst["public"]["net"]
    net = _clean(net)
    if net is None:
        return None, "bad net (shape/finite)"
    if h["kind"] == "rewrite":
        hit = lambda X: sum(1 for p in nn.predict(net, X) if p == h["cls"]) / len(X)
        keep = lambda X: sum(1 for a, b in zip(nn.predict(net, X), nn.predict(orig, X)) if a == b) / len(X)
        eff = 0.5 * hit(h["keysA"]) + 0.5 * hit(h["keysB"])
        spe = (200 * keep(h["ctlA"]) + 200 * keep(h["ctlN"]) + 150 * keep(h["ctlB"])) / 550
        return eff * spe, f"eff={eff:.2f} spec={spe:.2f}"
    k = h["layer"] - 1
    other = 1 - k
    if _clean(pub)["layers"][other] != net["layers"][other]:
        return 0.0, "other layer changed (spec 0)"
    ag = _agree(net, orig, h["test"])
    ad = h["agree_damaged"]
    return ag, f"agree={ag:.2f} damaged={ad:.2f}"


def reference(inst):
    h = inst["hidden"]
    if h["kind"] == "rewrite":
        return (REF_REWRITE.replace("@U@", str(h["planted_unit"])).replace("@F@", str(h["feature"]))
                .replace("@T@", repr(h["thr"])).replace("@C@", str(h["cls"])))
    return REF_REPAIR.replace("@FIX@", repr(json.dumps(h["fix"]))).replace("@LAYER@", str(h["layer"])).replace("@KIND@", repr(h["kind"]))


REF_REPAIR = """
import json
FIX = json.loads(@FIX@)
def edit(net, probe, spec):
    L = net["layers"][@LAYER@ - 1]
    if @KIND@ == "perm":
        p = FIX["perm"]
        W, b = [None] * len(p), [None] * len(p)
        for i, pi in enumerate(p):
            W[pi], b[pi] = L["W"][i], L["b"][i]
        L["W"], L["b"] = W, b
    else:
        for j, f in zip(FIX["rows"], FIX["factors"]):
            L["W"][j] = [w * f for w in L["W"][j]]
            L["b"][j] *= f
    return net
"""

REF_REWRITE = """
def edit(net, probe, spec):
    u, i, t, c = @U@, @F@, @T@, @C@
    L1, L2 = net["layers"]
    L1["W"][u] = [100.0 if d == i else 0.0 for d in range(len(L1["W"][u]))]
    L1["b"][u] = -100.0 * t
    for k in range(len(L2["W"])):
        L2["W"][k][u] = 1.0 if k == c else -1.0
    return net
"""

BASE = """
import time, math, random
import copy
CPU_LIMIT_S = 10.0
USE_VAL = False

def _score(net, X, Y):
    H = len(net["layers"][0]["W"]); loss = 0.0; ok = 0
    for x, y in zip(X, Y):
        h = [max(0.0, sum(w * v for w, v in zip(r, x)) + b) for r, b in zip(net["layers"][0]["W"], net["layers"][0]["b"])]
        z = [sum(w * v for w, v in zip(r, h)) + b for r, b in zip(net["layers"][1]["W"], net["layers"][1]["b"])]
        m = max(z); lse = m + math.log(sum(math.exp(v - m) for v in z))
        loss += lse - z[y]; ok += z.index(max(z)) == y
    return loss / len(X), ok / len(X)

def edit(net, probe, spec):
    t0 = time.process_time()
    budget = 0.7 * CPU_LIMIT_S
    rng = random.Random(0)
    orig = copy.deepcopy(net)
    X, Y = probe["x"], probe["y"]
    if USE_VAL:
        VX = [x for i, x in enumerate(X) if i % 3 == 2]; VY = [y for i, y in enumerate(Y) if i % 3 == 2]
        X = [x for i, x in enumerate(X) if i % 3 != 2]; Y = [y for i, y in enumerate(Y) if i % 3 != 2]
    n = len(X)
    L1, L2 = net["layers"]
    W1, b1, W2, b2 = L1["W"], L1["b"], L2["W"], L2["b"]
    H, D, C = len(W1), len(W1[0]), len(W2)
    train1 = spec["task"] == "rewrite" or spec["layer"] == 1
    train2 = spec["task"] == "rewrite" or spec["layer"] == 2
    if spec["task"] == "rewrite":
        for j in range(H):                      # unused units: give them a small random start so gradients can flow
            if not any(W1[j]) and not any(W2[c][j] for c in range(C)):
                W1[j] = [rng.gauss(0, 0.1) for _ in range(D)]
                for c in range(C):
                    W2[c][j] = rng.gauss(0, 0.1)
    lr, mom = 0.1, 0.9
    v1 = [[0.0] * D for _ in range(H)]; vb1 = [0.0] * H
    v2 = [[0.0] * H for _ in range(C)]; vb2 = [0.0] * C
    best = None
    ep = 0
    while time.process_time() - t0 < budget:
        ep += 1
        g1 = [[0.0] * D for _ in range(H)]; gb1 = [0.0] * H
        g2 = [[0.0] * H for _ in range(C)]; gb2 = [0.0] * C
        loss = 0.0; correct = 0
        for x, y in zip(X, Y):
            h = [0.0] * H
            for j in range(H):
                z = b1[j]
                for d in range(D):
                    z += W1[j][d] * x[d]
                h[j] = z if z > 0 else 0.0
            z2 = [b2[c] + sum(W2[c][j] * h[j] for j in range(H)) for c in range(C)]
            m = max(z2); e = [math.exp(v - m) for v in z2]; s = sum(e); p = [v / s for v in e]
            loss -= math.log(max(p[y], 1e-12)); correct += p.index(max(p)) == y
            dz = [p[c] - (1.0 if c == y else 0.0) for c in range(C)]
            dh = [0.0] * H
            for c in range(C):
                gb2[c] += dz[c]
                for j in range(H):
                    g2[c][j] += dz[c] * h[j]
                    dh[j] += W2[c][j] * dz[c]
            for j in range(H):
                if h[j] > 0:
                    gb1[j] += dh[j]
                    for d in range(D):
                        g1[j][d] += dh[j] * x[d]
        loss /= n
        if best is None or loss < best[0]:
            best = (loss, [r[:] for r in W1], b1[:], [r[:] for r in W2], b2[:])
        if correct == n and loss < 0.05:
            break
        k = 1.0 / n
        if train1:
            for j in range(H):
                vb1[j] = mom * vb1[j] - lr * gb1[j] * k; b1[j] += vb1[j]
                for d in range(D):
                    v1[j][d] = mom * v1[j][d] - lr * g1[j][d] * k; W1[j][d] += v1[j][d]
        if train2:
            for c in range(C):
                vb2[c] = mom * vb2[c] - lr * gb2[c] * k; b2[c] += vb2[c]
                for j in range(H):
                    v2[c][j] = mom * v2[c][j] - lr * g2[c][j] * k; W2[c][j] += v2[c][j]
    if best is not None and best[0] < loss:
        _, L1["W"], L1["b"], L2["W"], L2["b"] = best
    if USE_VAL and _score(net, VX, VY)[0] >= _score(orig, VX, VY)[0]:
        return orig                      # no gain on held-out probe points: keep the original weights
    return net
"""


def baseline(inst):
    return BASE


PROMPT = """You are writing one Python 3 module (standard library only) that patches the weights of a small neural network. Reply with exactly one ```python code block containing the whole module, and nothing else of substance. Your reply may use at most 8,000 tokens.

The network. A sorting robot classifies 8-number sensor readings into 4 classes with a fully connected net: input (8) -> hidden layer 1 (16 units, ReLU) -> layer 2 (4 units, softmax). The prediction is the argmax class. A net is a dict {"layers": [L1, L2]} (a Python list, so layer 1 is net["layers"][0] and layer 2 is net["layers"][1]), each layer {"W": rows (out x in, list of lists), "b": biases, "act": ...}. Layer 1 has W of 16 x 8 and layer 2 has W of 4 x 16. Two of the 16 hidden units are unused spares (their weights in and out are all exactly zero). Output shapes must stay exactly 16x8 / 16 / 4x16 / 4 with finite numbers of absolute value below 1e12.

Your task. @TASK@

Interface. Define `edit(net, probe, spec)` and return the patched net (same dict format; you may modify `net` in place and return it). Your module can `import nn`, a read-only helper in the same folder: nn.load() -> net, nn.forward(net, X) -> list of 4-vectors of class probabilities, nn.acts(net, X) -> acts[layer][i] = output vector of that layer for input i, nn.predict(net, X) -> class indices, nn.params(net), nn.nonzero(net), nn.evaluate(net, probe) -> {"acc", "loss", "n"}. `probe` is {"x": [8-vectors], "y": [class indices], "tag": [...]}: a small PUBLIC probe set of 24 labelled readings, deliberately taken from a narrow part of input space, so it is a hint, not a test set. `spec` is a dict describing the task: {"task": "repair", "layer": 1 or 2, "damage": "perm" | "flip" | "scale"} or {"task": "rewrite", "feature": i, "threshold": t, "cls": c}. For REWRITE the probe holds 6 key readings (label = the new class) and 18 control readings (label = what the net predicted before). A driver calls `edit` once. A module-level float `CPU_LIMIT_S` (default 10.0) is the CPU-time limit; exceeding it scores zero, so budget your own time (e.g. time.process_time()) and keep a margin.

Scoring (done outside the sandbox, on fresh hidden inputs drawn from a standard normal distribution, never from the probe). REPAIR: the share of fresh inputs on which the patched net predicts the same class as the undamaged net; if you change anything outside the named layer the score is 0. REWRITE: (hit rate of the new class on fresh key inputs, including inputs with a wider spread) times (share of fresh non-key inputs, including ones just outside the key region, whose prediction is unchanged). So returning the net untouched, setting every output to the new class, or zeroing the net all score about 0. A generic fit to the probe set alone is a weak strategy. Understand the network and the fault, and edit it deliberately.
"""
