"""Trusted copy of the COMBSYN rewrite calculus (vendored from crucibleforge gen/combsyn.py) + dedup enumerator.
Terms: a constant name (str) or a 2-tuple (function, argument). Rules: {name: (arity, body)}; body uses int arg indexes."""


def parse(text, names):
    toks = [ch for ch in str(text) if not ch.isspace()]
    pos = [0]

    def atom():
        if pos[0] >= len(toks):
            raise ValueError("unexpected end of term")
        ch = toks[pos[0]]
        pos[0] += 1
        if ch == "(":
            t = seq()
            if pos[0] >= len(toks) or toks[pos[0]] != ")":
                raise ValueError("missing )")
            pos[0] += 1
            return t
        if ch not in names:
            raise ValueError("symbol not allowed: " + ch)
        return ch

    def seq():
        t = atom()
        while pos[0] < len(toks) and toks[pos[0]] != ")":
            t = (t, atom())
        return t
    t = seq()
    if pos[0] != len(toks):
        raise ValueError("unbalanced )")
    return t


def size(t):
    n, stack = 0, [t]
    while stack:
        u = stack.pop()
        if isinstance(u, tuple):
            stack.extend(u)
        else:
            n += 1
    return n


def subst(body, args):
    if isinstance(body, tuple):
        return (subst(body[0], args), subst(body[1], args))
    return args[body]


def step(t, rules):
    args, head = [], t
    while isinstance(head, tuple):
        args.append(head[1])
        head = head[0]
    args.reverse()
    r = rules.get(head)
    if r is not None and len(args) >= r[0]:
        out = subst(r[1], args[:r[0]])
        for x in args[r[0]:]:
            out = (out, x)
        return out, True
    for i, x in enumerate(args):
        y, ch = step(x, rules)
        if ch:
            out = head
            for j, z in enumerate(args):
                out = (out, y if j == i else z)
            return out, True
    return t, False


def norm(t, rules, max_steps, max_size=4000):
    for _ in range(max_steps):
        t, ch = step(t, rules)
        if not ch:
            return t
        if size(t) > max_size:
            raise RuntimeError("term grew past the size guard")
    t2, ch = step(t, rules)
    if ch:
        raise RuntimeError("no result within the step limit")
    return t


def show(t, top=True):
    if not isinstance(t, tuple):
        return t
    s = show(t[0], True) + " " + show(t[1], False)
    return s if top else "(" + s + ")"


def try_norm(t, rules, steps, sz):
    try:
        return norm(t, rules, steps, sz)
    except RuntimeError:
        return None


def pure(t):
    stack = [t]
    while stack:
        u = stack.pop()
        if isinstance(u, tuple):
            stack.extend(u)
        elif u not in "abc":
            return False
    return True


def interesting(shown):
    letters = [ch for ch in shown if ch in "abc"]
    return 2 <= len(letters) <= 9 and len(set(letters)) >= 2


def body_text(body):
    return show(subst(body, list("xyz")))


def rules_from_struct(rl):
    """[{"name","arity","body"}] -> {name: (arity, body-tuple)}  (body text over x y z, same notation as terms)"""
    out = {}
    for r in rl:
        out[r["name"]] = (int(r["arity"]), _to_idx(parse(r["body"], "xyz")))
    return out


def _to_idx(b):
    return (_to_idx(b[0]), _to_idx(b[1])) if isinstance(b, tuple) else "xyz".index(b)


def struct_from_rules(rules):
    return [{"name": n, "arity": a, "body": body_text(b)} for n, (a, b) in sorted(rules.items())]


def enumerate_behaviours(rules, max_size, stats=None):
    """All closed terms up to max_size (deduplicated by normal form below max_size; the last size is probed without
    storing). -> {shown result of T a b c: (minimum size, witness term)} for results made only of a, b, c."""
    seen, by, beh = {}, {1: []}, {}

    def probe(t, sz, wit):
        r = try_norm((((t, "a"), "b"), "c"), rules, 80, 120)
        if r is not None and pure(r):
            k = show(r)
            if k not in beh:
                beh[k] = (sz, wit)
    for n in sorted(rules):
        nf = try_norm(n, rules, 60, 80)
        if nf is not None and nf not in seen:
            seen[nf] = n
            by[1].append(nf)
            probe(nf, 1, n)
    for sz in range(2, max_size + 1):
        last, by[sz] = sz == max_size, []
        for i in range(1, sz):
            for f in by[i]:
                for x in by[sz - i]:
                    if last:
                        probe((f, x), sz, (seen[f], seen[x]))
                        continue
                    nf = try_norm((f, x), rules, 60, 80)
                    if nf is not None and nf not in seen:
                        seen[nf] = (seen[f], seen[x])
                        by[sz].append(nf)
                        probe(nf, sz, seen[nf])
    if stats is not None:
        stats.update({sz: len(v) for sz, v in by.items()})
        stats['pairs'] = {sz: sum(len(by[i]) * len(by[sz - i]) for i in range(1, sz)) for sz in range(2, max_size + 1)}
    return beh
