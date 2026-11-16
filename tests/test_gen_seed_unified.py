"""One seed derivation: every family derives its instance seed through gen.instance_seed."""
import pytest
from crucibleforge import gen

GENS = {
    "keyfunc": {"level": 2}, "tracevm": {"level": 2}, "ledger": {"level": 1},
    "istack": {"level": 2, "variant": "write"}, "session_state": {"level": 2, "variant": "state"},
}


@pytest.mark.parametrize("fam", sorted(GENS))
def test_family_uses_registry_seed(fam, monkeypatch):
    calls = []
    real = gen.instance_seed

    def spy(*a, **k):
        calls.append(a[0] if a else k.get("family"))
        return real(*a, **k)

    monkeypatch.setattr(gen, "instance_seed", spy)
    mod = gen.get(fam)
    for m in (mod, ):
        # drop per-module caches so the derivation runs
        for n in dir(m):
            f = getattr(m, n)
            if hasattr(f, "cache_clear"):
                f.cache_clear()
    mod.materialize(dict(GENS[fam], type=fam), release_seed="u1", case_id="X0")
    assert fam in calls
