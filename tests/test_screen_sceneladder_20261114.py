"""screen-sceneladder (rig, R1-R7 x3) and screen-sceneladder-h (hosted, R1/R3/R5 x3): both load and grade, every
cell is tagged, the session line matches screen-sceneshift (16,384 per turn, history 49,000, ran_out exclude, fits the
73,728 load), 0 points, and the hosted subset draws the same instances as the rig screen."""
from crucibleforge import chains, profiles


def _load(monkeypatch, comp):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, comp)
    return profiles.load_profile("bench45")


def _flats(prof):
    return {fs["name"]: fs for s in chains.sections(prof) for fs in s["flat"]}


def test_screen_sceneladder_rig_and_hosted(monkeypatch):
    rig, host = _load(monkeypatch, "screen-sceneladder"), _load(monkeypatch, "screen-sceneladder-h")
    for prof in (rig, host):
        chains.check_gradeable(prof)
        assert prof["scores"]["chat"]["parts"]["sc_ladder"]["max"] == 0
    fr, fh = _flats(rig), _flats(host)
    assert sorted(fs["item"]["level"] for fs in fr.values()) == [1, 2, 3, 4, 5, 6, 7]
    assert sorted(fs["item"]["level"] for fs in fh.values()) == [1, 3, 5] and set(fh) <= set(fr)
    for fs in list(fr.values()) + list(fh.values()):
        it = fs["item"]
        assert fs["count"] == 3 and it["family"] == "sceneladder" and it.get("concept") and it.get("band")
        assert it["ceiling"] == 16384 and it["history_budget"] == 49000 and it["ran_out"] == "exclude"
        assert it["ceiling"] + it["history_budget"] + 8192 <= 73728
    ids_r = {c["id"]: c for c in chains.chain_cases(rig)}
    ids_h = {c["id"]: c for c in chains.chain_cases(host)}
    assert len(ids_r) == 21 and len(ids_h) == 9 and set(ids_h) <= set(ids_r)
    for cid in ids_h:
        a = chains.materialize(chains.instance(ids_h[cid], 1, 1, release_seed="4.5.0"))
        b = chains.materialize(chains.instance(ids_r[cid], 1, 1, release_seed="4.5.0"))
        assert a["turns"] == b["turns"]
