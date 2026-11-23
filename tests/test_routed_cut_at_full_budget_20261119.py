"""A routed composition with cut_at_full_budget: zero is refused at load (routed compositions are not part of 4.5)."""
import pytest

from crucibleforge import routing
from crucibleforge.config import ConfigError


def test_routed_plus_zero_is_refused():
    with pytest.raises(ConfigError, match="routed compositions are not part of 4.5"):
        routing.spec({"name": "p", "routing": {"calibration": "x.json"}, "cut_at_full_budget": "zero"})


def test_no_routing_block_is_untouched():
    assert routing.spec({"name": "p", "cut_at_full_budget": "zero"}) is None


def test_the_shipped_routed_compositions_state_the_plain_rule(monkeypatch):
    from crucibleforge import chains, profiles
    for comp in ("main-routed", "rc1-main"):
        monkeypatch.setenv(profiles.COMPOSITION_ENV, comp)
        prof = profiles.load_profile("bench45")
        assert prof.get("routing") and chains.cut_at_full_budget(prof) == "omit"
        routing.spec(prof)
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "standard")
    assert chains.cut_at_full_budget(profiles.load_profile("bench45")) == "zero"
