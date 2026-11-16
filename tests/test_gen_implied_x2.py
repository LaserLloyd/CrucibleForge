"""screen-implied-x2 (lead 2026-11-14 ~12:10): two IMPLIED briefs per generation on the SAME briefs as main-v1's x3
line. Brief i of the x3 set (unit (i-1)//3+1, sub-item (i-1)%3+1) is brief i of the x2 set (unit (i-1)//2+1, sub-item
(i-1)%2+1): identical grader_config (domain, seed, tier, variant), per-brief text and hidden check list. The x3 units
listed in the screen keep main-v1's ids and chain stamps, so stored x3 rows stay current and are not re-run."""
import pytest

from crucibleforge import chains, gen, profiles
from crucibleforge.gen import implied as I

RS = "x2-identity-test"


def _cases(monkeypatch, comp):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, comp)
    prof = profiles.load_profile("bench45")
    chains.check_gradeable(prof)
    return prof, {c["id"]: c for c in chains.chain_cases(prof)}


def _briefs(cases, ladder):
    """[(grader_config, text, checks)] in brief order i = 1.. of a packed implied set."""
    out = []
    for cid in sorted(c for c in cases if c.startswith(f"FLAT-{ladder}-")):
        case = chains.materialize(chains.instance(cases[cid], 1, 1, release_seed=RS))
        for sub in case["multi_items"]:
            cfg = dict(sub["grader_config"])
            inst = I._instance(int(cfg["seed"]), int(cfg["tier"]), cfg.get("domain") or sub["meta"]["domain"],
                               cfg.get("variant"))
            checks = [(e["id"], e["kind"], e.get("scen")) for e in inst["hidden"]["exps"]]
            cfg.pop("tier_tags", None)
            out.append((cfg, sub["prompt"], checks))
    return out


@pytest.mark.parametrize("x3,x2,n_x2", [("implied1x3_L1", "implied1x2_L1", 12), ("implied1x3h_L3", "implied1x2h_L3", 6)])
def test_x2_briefs_are_the_x3_briefs_in_order(monkeypatch, x3, x2, n_x2):
    _p, main = _cases(monkeypatch, "main-v1")
    _p, scr = _cases(monkeypatch, "screen-implied-x2")
    assert sum(1 for c in scr if c.startswith(f"FLAT-{x2}-")) == n_x2
    a, b = _briefs(main, x3), _briefs(scr, x2)
    assert len(a) == len(b) == 2 * n_x2
    for i, (ra, rb) in enumerate(zip(a, b), 1):
        assert ra[0] == rb[0], f"brief {i}: grader_config differs"
        assert ra[1] == rb[1], f"brief {i}: text differs"
        assert ra[2] == rb[2], f"brief {i}: hidden checks differ"
    assert len({r[1] for r in a}) >= len(a) - 1      # (one L3 text recurs on purpose: same thermo brief, other seed)


def test_x3_units_in_the_screen_keep_main_v1_ids_and_stamps(monkeypatch):
    for comp in ("main-v1", "main-v1-imp", "main-v1-ref"):
        _p, main = _cases(monkeypatch, comp)
        _p, scr = _cases(monkeypatch, "screen-implied-x2")
        ids = [c for c in main if c.startswith(("FLAT-implied1x3_L1-", "FLAT-implied1x3h_L3-"))]
        assert len(ids) == 12
        for cid in ids:
            ia = chains.instance(main[cid], 1, 1, release_seed=RS)["chain"]
            ib = chains.instance(scr[cid], 1, 1, release_seed=RS)["chain"]
            assert chains.revision(ia, RS) == chains.revision(ib, RS), cid


def test_x2_sets_ceilings_rule_and_points(monkeypatch):
    prof, scr = _cases(monkeypatch, "screen-implied-x2")
    fl = {fs["name"]: fs for s in chains.sections(prof) for fs in s["flat"]}
    assert fl["implied1x2_L1"]["n"] == fl["implied1x2h_L3"]["n"] == 2
    assert fl["implied1x2_L1"]["item"]["ceiling"] == 63000
    assert fl["implied1x2h_L3"]["item"]["ceiling"] == 65536
    assert all(fl[s]["item"].get("ran_out") == "exclude" for s in ("implied1x2_L1", "implied1x2h_L3"))
    assert all(fl[s]["element"] == "check" for s in ("implied1x2_L1", "implied1x2h_L3"))
    assert prof["scores"]["work"]["parts"]["bk_implied"]["max"] == 0
