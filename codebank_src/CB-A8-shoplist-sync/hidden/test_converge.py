import itertools

from _sim import converge, random_ops
from shoplist import Replica


def test_merge_order_does_not_matter():
    for seed in range(60):
        reps = random_ops(seed)
        states = {repr(converge(reps, order).state()) for order in itertools.permutations(range(3))}
        assert len(states) == 1, seed


def test_idempotent_and_associative():
    for seed in range(40):
        a, b, c = random_ops(seed)
        x = Replica.load(a.dump()); x.merge(b); x.merge(c)
        y = Replica.load(b.dump()); y.merge(c)
        z = Replica.load(a.dump()); z.merge(y)
        assert x.state() == z.state(), seed
        before = x.state()
        x.merge(x); x.merge(Replica.load(b.dump()))
        assert x.state() == before
