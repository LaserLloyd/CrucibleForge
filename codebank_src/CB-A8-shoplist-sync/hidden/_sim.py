import random

from shoplist import Replica

NAMES = ["Oat Milk", "oat  milk", "Eggs", "eggs ", "Bread", "Coffee", "rice"]


def random_ops(seed, n_rep=3, n_ops=40):
    """Replicas doing random ops with random pairwise syncs; returns replicas."""
    rnd = random.Random(seed)
    reps = [Replica(r) for r in "abc"[:n_rep]]
    for _ in range(n_ops):
        r = rnd.choice(reps)
        op = rnd.random()
        name = rnd.choice(NAMES)
        if op < 0.35:
            r.add(name, rnd.randint(1, 5))
        elif op < 0.5:
            r.remove(name)
        elif op < 0.6:
            r.set_qty(name, rnd.randint(1, 9))
        elif op < 0.7:
            r.check(name, rnd.random() < 0.5)
        else:
            o = rnd.choice(reps)
            if o is not r:
                r.merge(o)
    return reps


def converge(reps, order):
    """Merge every replica's state into a fresh copy, in the given order."""
    hub = Replica("zz")
    for i in order:
        hub.merge(Replica.load(reps[i].dump()))
    return hub
