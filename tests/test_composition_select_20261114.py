"""Selectable compositions (2026-11-14): CF_COMPOSITION picks one of bench45's `compositions:`; unset is
the profile's default_composition (`standard` since the cut; `default` = the base, by name); an unknown name is an error, never a silent fallback."""
import pytest

from crucibleforge import config, profiles

CFG = config.load_config(config.EXAMPLE_CONFIG_PATH)


def _main(prof, sec):
    return {k: v["max"] for k, v in prof["scores"][sec]["parts"].items() if v.get("max")}


def test_unset_is_the_shipping_standard_and_default_stays_selectable(monkeypatch):
    """The cut (lead 2026-11-15) changed the default on purpose: unset = bench45's default_composition
    `standard`; the base composition is still there by name."""
    monkeypatch.delenv(profiles.COMPOSITION_ENV, raising=False)
    assert profiles.load_profile("bench45", CFG)["composition"] == "standard"
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "default")
    p = profiles.load_profile("bench45", CFG)
    assert p["composition"] == "default"
    assert _main(p, "work") == {"mc1_functions": 3, "mc3_fix": 3, "mc4_solve": 4}


def test_rc1_night_replaces_main_and_keeps_the_rest(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc1-night")
    p = profiles.load_profile("bench45", CFG)
    assert p["composition"] == "rc1-night"
    # levels updated 00:5x (COMPOSE.md): points follow element counts, attend gone
    assert _main(p, "work") == {"rn_trace": 3.75, "rn_keyfunc": 6.25}
    assert _main(p, "chat") == {"rn_istack": 6.25, "rn_state": 3.75}
    assert sum(_main(p, "nsfw").values()) == 10                       # NSFW stays as merged
    parts = p["scores"]["work"]["parts"]
    assert len(parts["rn_trace"]["cases"]) == 6 and len(parts["rn_keyfunc"]["cases"]) == 10
    by_id = {c["id"]: c for c in profiles.core_generated_cases(p)}
    assert by_id["TRACEVM-core-8-1"]["budget"] == 49152 and by_id["KEYFUNC-core-4-1"]["budget"] == 42000
    assert "budget" not in by_id["SESSION_STATE-multi_hard-core-10-1"]
    assert not any(i.startswith("ATTEND") for i in by_id)
    assert "TZ16-pickup-dst-delay" not in str(p["cases"])             # stored items carry no points
    assert p["recommended_samplers"] == profiles.load_profile("bench", CFG)["recommended_samplers"]


def test_unknown_composition_is_refused(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "nope")
    with pytest.raises(config.ConfigError, match="unknown composition 'nope'"):
        profiles.load_profile("bench45", CFG)


def test_rc1_night_full_uses_flat_elements(monkeypatch):
    """The FULL rc1-night form (chains.py flat elements), levels updated 00:5x: tracevm L8 single items,
    keyfunc / istack-write 5-item prompts, session_state x6 the main Chat element, attend 2 items at 0 points
    (measurement only); every family gradeable with no judge."""
    from crucibleforge import chains
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc1-night-full")
    p = profiles.load_profile("bench45", CFG)
    assert _main(p, "work") == {"rn_trace": 3.75, "rn_keyfunc": 6.25}
    assert _main(p, "chat") == {"rn_istack": 6.25, "rn_state": 3.75}
    assert p["scores"]["chat"]["parts"]["rn_attend"]["max"] == 0
    assert not profiles.core_generated_cases(p)
    chains.check_gradeable(p)
    cs = chains.chain_cases(p)
    by = {}
    for c in cs:
        by.setdefault(c["chain_spec"]["part"], []).append(c["chain_spec"])
    assert len(by["work/rn_trace"]) == 6 and all(len(s.get("items") or [1]) == 1 for s in by["work/rn_trace"])
    assert len(by["work/rn_keyfunc"]) == 2 and len(by["chat/rn_istack"]) == 2
    assert all(len(s["items"]) == 5 for s in by["work/rn_keyfunc"] + by["chat/rn_istack"])
    assert len(by["chat/rn_attend"]) == 2 and all(s["element"] == "turn" for s in by["chat/rn_attend"])
    assert len(by["chat/rn_state"]) == 6
    ceil = {k: {s["rungs"][0]["ceiling"] for s in v} for k, v in by.items()}
    assert ceil == {"work/rn_trace": {49152}, "work/rn_keyfunc": {65536}, "chat/rn_istack": {65536},
                    "chat/rn_attend": {2500}, "chat/rn_state": {16384}}  # 2500 -> 16384: K traced 75% "ran out" at 2,500 (lead 2026-11-14 07:0x)
