"""nn.py: tiny pure-Python scaffold for NETSURGEON. A net is {"layers": [{"W": out x in, "b": out, "act": "relu"|"softmax"|"identity"}, ...]}.
load() -> the net you must edit.  forward(net, X) -> list of probability vectors.  acts(net, X) -> acts[layer][i] = output vector of
layer `layer` for input i.  predict(net, X) -> class indices.  params(net) -> number of weights.  nonzero(net) -> number of non-zero weights.
evaluate(net, probe) -> {"acc", "loss", "n"} on the PUBLIC probe set only (probe = {"x": [...], "y": [...], "tag": [...]})."""
import json, math, copy


def load(path="in.json"):
    return copy.deepcopy(json.load(open(path, encoding="utf-8"))["net"])


def _softmax(z):
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]


def layer_forward(L, x):
    z = [sum(w * v for w, v in zip(row, x)) + bb for row, bb in zip(L["W"], L["b"])]
    a = L["act"]
    if a == "relu":
        return [v if v > 0 else 0.0 for v in z]
    if a == "softmax":
        return _softmax(z)
    return z


def acts(net, X):
    out = [[] for _ in net["layers"]]
    for x in X:
        v = x
        for i, L in enumerate(net["layers"]):
            v = layer_forward(L, v)
            out[i].append(v)
    return out


def forward(net, X):
    return acts(net, X)[-1]


def predict(net, X):
    return [max(range(len(p)), key=p.__getitem__) for p in forward(net, X)]


def params(net):
    return sum(len(L["W"]) * len(L["W"][0]) + len(L["b"]) for L in net["layers"])


def nonzero(net):
    return sum(1 for L in net["layers"] for r in L["W"] for w in r if w != 0) + sum(1 for L in net["layers"] for w in L["b"] if w != 0)


def evaluate(net, probe):
    P = forward(net, probe["x"])
    y = probe["y"]
    acc = sum(1 for p, t in zip(P, y) if max(range(len(p)), key=p.__getitem__) == t) / len(y)
    loss = -sum(math.log(max(p[t], 1e-12)) for p, t in zip(P, y)) / len(y)
    return {"acc": acc, "loss": loss, "n": len(y)}
