"""screen-knowgap composition (segment H6): loads, every unit gradeable without a judge, the planned shape."""
from crucibleforge import chains, config, profiles

CFG = config.load_config(config.EXAMPLE_CONFIG_PATH)


def test_screen_knowgap_shape(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "screen-knowgap")
    p = profiles.load_profile("bench45", CFG)
    assert p["scores"]["chat"]["parts"]["sc_know"]["max"] == 0
    chains.check_gradeable(p)
    cs = chains.chain_cases(p)
    specs = [c["chain_spec"] for c in cs if c["chain_spec"]["part"] == "chat/sc_know"]
    assert len(specs) == 12
    levels = sorted(s["rungs"][0]["level"] for s in specs)
    assert levels == [1, 1, 1, 2, 2, 2, 3, 3, 3, 5, 5, 5]
    assert {s["rungs"][0]["ceiling"] for s in specs} == {24576, 32768}
