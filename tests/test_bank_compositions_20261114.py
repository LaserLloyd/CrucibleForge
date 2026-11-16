"""4.5 bank compositions (Admin G2, CHECK-1): every one loads, is fully tagged, and a composition's
pack_exclude takes its Main anchors out of the packs (added to the profile's list, never replacing it)."""
import pytest

from crucibleforge import chains, ladder, profiles, routing

BANK = ["main-v1", "locator", "mod-low", "mod-mid", "mod-high", "pack-hardcode", "pack-hardchat", "calib-1",
        "main-routed", "pack-unknown", "calib-2", "screen-local-1", "screen-headroom-ref", "screen-headroom-ref-nod", "screen-planopt-rig",
        "pack-seeds-bbox", "pack-seeds-probe", "pack-seeds-trace", "rc1-main", "rc2-main"]


def _load(monkeypatch, comp):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, comp)
    return profiles.load_profile("bench45")


@pytest.mark.parametrize("comp", BANK)
def test_bank_composition_loads_tagged_and_gradeable(monkeypatch, comp):
    prof = _load(monkeypatch, comp)
    secs = chains.sections(prof)
    chains.check_gradeable(prof)
    cells = [fs["item"] for s in secs for fs in s["flat"]]
    assert cells and all(c.get("concept") and c.get("band") for c in cells)
    # IMPLIED level 5 never scores a ran-out row as 0 (its 65,536 ceiling is unvalidated)
    assert all(c.get("ran_out") == "exclude" for c in cells if c["family"] == "implied" and c["level"] == 5)


def test_shared_set_names_pair_across_bank_compositions(monkeypatch):
    names = {}
    for comp in ("main-v1", "locator", "mod-low", "mod-mid"):
        prof = _load(monkeypatch, comp)
        names[comp] = {fs["name"] for s in chains.sections(prof) for fs in s["flat"]}
    assert "implied1x3_L1" in names["main-v1"] & names["locator"] & names["mod-low"] & names["mod-mid"]
    assert "session_state_multi_hard_L10" in names["main-v1"] & names["locator"]


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
def test_composition_pack_exclude_adds_to_the_profile_list(monkeypatch):
    base = _load(monkeypatch, "default")
    found = [s["n"] for v in ladder.discover(base).values() for s in v if "CB-C5-predict-pyhouse" in s["cases"]]
    assert found, "CB-C5 sits in a pack file under the default composition"
    prof = _load(monkeypatch, "calib-1")
    assert set(base.get("pack_exclude") or []) <= set(prof["pack_exclude"])
    assert {"CB-C5-predict-pyhouse", "CL8-machine-plan"} <= set(prof["pack_exclude"])
    assert not [s for v in ladder.discover(prof).values() for s in v
                if {"CB-C5-predict-pyhouse", "CL8-machine-plan"} & set(s["cases"])]


def test_main_routed_routing_block(monkeypatch):
    prof = _load(monkeypatch, "main-routed")
    sp = routing.spec(prof)
    assert sp["cuts"] == [-1.0, 0.6] and sp["straddle"] == "half"
    assert sp["calibration"] == "calibration/calibration-g2-1.json"
    assert [m["name"] for m in sp["modules"]] == ["low", "mid", "top"]
    assert [m["unlock"] for m in sp["modules"]] == [False, False, True]
    # top carries Main cells of its own, not only the packs
    top = sp["modules"][2]["sets"]
    assert [n for n in top if "_pack" not in n]
    # the 8B (-1.5) routes low, the reference (0) mid, Sonnet (+1.14) top with the packs unlocked
    for theta, name, unlock in ((-1.5, "low", False), (0.0, "mid", False), (1.14, "top", True)):
        r = routing.choose(theta, 0.2, sp)
        assert [m["name"] for m in r["modules"]] == [name] and r["unlock"] is unlock
    # every routed cell outside the packs sits in the locator or a module; the pack cells only in top
    packs = {n for m in sp["modules"] for n in m["sets"] if "_pack" in n}
    assert packs and packs <= set(top)


def _prompt(cs, cid):
    m = chains.materialize(chains.instance(cs[cid], 1, 1, release_seed="4.5.0"))
    return str(m.get("prompt") or m.get("turns"))


@pytest.mark.parametrize("alias,target", [
    ("FLAT-rt_implied_lo_L1-004", "FLAT-rt_implied1_L1-004"),
    ("FLAT-rt_implied3_top_L3-003", "FLAT-rt_implied3_L3-003"),
    ("FLAT-rt_pytrap_lo_L1-002", "FLAT-rt_pytrap_mid_L1-002"),
    ("FLAT-rt_state_lo_L10-003", "FLAT-rt_state_mid_L10-003"),
    ("FLAT-rt_istack_lo4_L4-001", "FLAT-rt_istack_mid_L4-001"),
    ("FLAT-rt_istack_top7_L7-001", "FLAT-rt_istack_mid_L7-001"),
])
def test_main_routed_neighbours_share_instances(monkeypatch, alias, target):
    """Models either side of a cut answer the SAME instances on the shared cells (paired comparison)."""
    prof = _load(monkeypatch, "main-routed")
    cs = {c["id"]: c for c in chains.chain_cases(prof)}
    assert _prompt(cs, alias) == _prompt(cs, target)


def test_seed_as_onto_a_packed_target_repacked(monkeypatch):
    """seed_as onto a packed target: unit i = the target's unit i whatever either side's packing
    (before the fix an alias seeded on <set>-<i>-r1-a1 while the packed target seeded its
    sub-item on <unit>-i<k>, so istack aliases drew other texts)."""
    prof = _load(monkeypatch, "main-routed")
    secs = chains.sections(prof)
    fs = next(f for s in secs for f in s["flat"] if f["name"] == "rt_istack_mid_L7")
    al = next(f for s in secs for f in s["flat"] if f["name"] == "rt_istack_top7_L7")
    # packing is a profile choice (5 per prompt until the H1 fairness pass, 2 since): the property holds for any n >= 2
    assert al.get("seed_ladder") == "rt_istack_mid_L7" and al.get("seed_n") == fs["n"] >= 2
    cs = {c["id"]: c for c in chains.chain_cases(prof)}
    sub_t = chains.materialize(chains.instance(cs["FLAT-rt_istack_mid_L7-001"], 1, 1, release_seed="4.5.0"))["multi_items"]
    # a single-item alias (n = 1) of the packed target: its unit k is the target's sub-item k (k = the last sub-item)
    k = fs["n"]
    single = dict(cs["FLAT-rt_istack_top7_L7-001"]["chain_spec"])
    single.pop("items")
    single.update({"kind": "flat", "c": k})
    one = chains.materialize(chains.instance({"chain_spec": single}, 1, 1, release_seed="4.5.0"))
    assert chains._sub_prompt(one) == chains._sub_prompt(sub_t[k - 1])


def test_screen_local_1_shares_calib2_sceneshift_and_fits_the_window(monkeypatch):
    """screen-local-1 (Admin G2): SCENESHIFT reuses calib-2's instances (same set names, same line), fits the loaded
    73,728 window, and scores at 0 points."""
    prof = _load(monkeypatch, "screen-local-1")
    chains.check_gradeable(prof)
    flats = {fs["name"]: fs for s in chains.sections(prof) for fs in s["flat"]}
    cal = {fs["name"]: fs for s in chains.sections(_load(monkeypatch, "calib-2-noimp")) for fs in s["flat"]}
    assert set(flats) == {"sceneshift_L1", "sceneshift_L4", "sceneshift_L7"}
    for nm, fs in flats.items():
        assert fs["count"] == 3 and nm in cal
        a, b = fs["item"], cal[nm]["item"]
        for k in ("family", "level", "ceiling", "history_budget", "ran_out"):
            assert a.get(k) == b.get(k), (nm, k)
        assert a["ceiling"] == 16384 and a["ceiling"] + a["history_budget"] + 8192 <= 73728
    ids = {c["id"] for c in chains.chain_cases(prof)}
    assert {f"FLAT-sceneshift_L{lv}-001" for lv in (1, 4, 7)} <= {c["id"] for c in chains.chain_cases(
        _load(monkeypatch, "calib-2-noimp"))} and len(ids) == 9
    assert prof["scores"]["chat"]["parts"]["bk_scene"]["max"] == 0


def test_pack_hardcode_carries_the_c4_optimisation_cell(monkeypatch):
    # seg PACKS (Nov 14): planted_opt L4 is the C4 top cell; its 65,536 ceiling rests on DeepSeek flash's passes
    # (31-42k) and is not validated for the strongest thinkers, so a ran-out row is excluded, never scored 0
    prof = _load(monkeypatch, "pack-hardcode")
    cells = [fs["item"] for s in chains.sections(prof) for fs in s["flat"] if fs["item"]["family"] == "planted_opt"]
    assert [(c["level"], c["concept"], c["band"], c["ran_out"]) for c in cells] == [(4, "C4", "top", "exclude")]


def test_pack_unknown_three_r1_cells(monkeypatch):
    """pack-unknown (Admin G2): BLACK BOX L3 / NETSURGEON L1 / PROBE L3 x4, ceilings and ran-out as decided in 059463a."""
    prof = _load(monkeypatch, "pack-unknown")
    chains.check_gradeable(prof)
    got = {fs["name"]: (fs["count"], fs["item"]["ceiling"], fs["item"].get("ran_out") or "zero", fs["item"]["band"])
           for s in chains.sections(prof) for fs in s["flat"]}
    assert got == {"blackbox_L3": (4, 131072, "zero", "top"), "netsurgeon_L1": (4, 98304, "zero", "high"),
                   "probe_L3": (4, 131072, "exclude", "top")}


@pytest.mark.parametrize("seeds,pack,name", [("pack-seeds-bbox", "pack-unknown", "blackbox_L3"),
                                             ("pack-seeds-probe", "pack-unknown", "probe_L3"),
                                             ("pack-seeds-trace", "pack-hardcode", "tracevm_L8")])
def test_pack_seed_compositions_extend_the_pack_cell(monkeypatch, seeds, pack, name):
    """pack-seeds-* (seg PACKS, Nov 14): one pack cell at x12 under the pack's own set name and line, 0 points, so the
    stored seed rows resolve and units 001-004 are the pack's own instances (byte-identical prompts)."""
    sp = _load(monkeypatch, seeds)
    chains.check_gradeable(sp)
    flats = {fs["name"]: fs for s in chains.sections(sp) for fs in s["flat"]}
    assert set(flats) == {name} and flats[name]["count"] == 12
    assert sp["scores"]["work"]["parts"]["bk_trace" if "trace" in name else "bk_unknown"]["max"] == 0
    seed_cs = {c["id"]: c for c in chains.chain_cases(sp)}
    assert len(seed_cs) == 12
    pp = _load(monkeypatch, pack)
    pf = next(fs for s in chains.sections(pp) for fs in s["flat"] if fs["name"] == name)
    a, b = flats[name]["item"], pf["item"]
    for k in ("family", "level", "ceiling", "ran_out", "concept", "band", "element", "element_weight", "average"):
        assert a.get(k) == b.get(k), (name, k)
    pack_cs = {c["id"]: c for c in chains.chain_cases(pp)}
    for i in range(1, 5):
        cid = f"FLAT-{name}-{i:03d}"
        assert _prompt(seed_cs, cid) == _prompt(pack_cs, cid)


def test_pytrap_ceiling_32768_on_every_composition(monkeypatch):
    """Admin G2 / lead 2026-11-14: PYTRAP's 16,384 cut the 8B (all thinking) and DeepSeek flash (L3 7 of 9); every PYTRAP
    line, Main / locator / modules / calib / screens, runs at 32,768 with ran-out excluded."""
    import yaml
    comps = yaml.safe_load(open(profiles.PROFILES_DIR / "bench45.yaml" if hasattr(profiles, "PROFILES_DIR")
                                else __import__("pathlib").Path(__file__).parents[1] / "profiles" / "bench45.yaml", encoding="utf-8"))["compositions"]
    seen = 0
    for name in comps:
        try:
            prof = _load(monkeypatch, name)
            secs = chains.sections(prof)
        except Exception:
            continue
        for s in secs:
            for fs in s["flat"]:
                if fs["item"]["family"] == "pytrap":
                    seen += 1
                    # rc1-main: PYTRAP L1 ran_out zero (lead 2026-11-14, non-converging spirals at 3.6x the
                    # reference's largest correct answer); every other composition keeps exclude
                    want = "zero" if name == "rc1-main" and fs["item"]["level"] == 1 else "exclude"
                    assert fs["item"]["ceiling"] == 32768 and fs["item"].get("ran_out", "zero") == want, (name, fs["name"])
    assert seen >= 15


def test_screen_headroom_ref_lines(monkeypatch):
    """screen-headroom-ref (Admin G2, lead 2026-11-14): the reference on the hardest existing Chat / C5 levels, 0 points,
    own set names, ran-out excluded, every session line fits the loaded 73,728 window, the cut part is last."""
    prof = _load(monkeypatch, "screen-headroom-ref")
    secs = chains.sections(prof)
    flat = [fs for s in secs for fs in s["flat"]]
    cells = {(fs["item"]["family"], fs["item"]["level"]) for fs in flat}
    assert {("session_ledger", 5), ("istack", 3), ("knowgap", 6), ("knowgap", 7), ("toolworld", 6),
            ("toolworld", 7), ("istack", 8), ("sceneshift", 7)} <= cells
    assert all(fs["name"].startswith("hr_") for fs in flat)
    assert all(fs["item"]["ran_out"] == "exclude" for fs in flat)
    for fs in flat:
        it = fs["item"]
        if it.get("history_budget"):
            assert it["ceiling"] + it["history_budget"] + 8192 <= 73728, fs["name"]
    import yaml, pathlib
    comp = yaml.safe_load(open(pathlib.Path(__file__).parents[1] / "profiles" / "bench45.yaml", encoding="utf-8"))["compositions"]["screen-headroom-ref"]
    assert list(comp["chains"])[-1] == "chat/hr_d"          # D's cut point: the last part
    for sec in comp["scores"].values():
        assert all(p["max"] == 0 for p in sec["parts"].values())
    cases = chains.chain_cases(prof)
    assert len(cases) == 4 + 3 + 6 + 6 + 2 + 2          # ledger L5 x4, istack L3 x5 x3, KNOWGAP, toolworld, hr_d
    assert not any("attend" in c["id"] for c in cases)


def test_screen_planopt_rig_fits_the_window(monkeypatch):
    """seg CHAT-TOP: PLANOPT L1-L3 x 2 sessions under the set name planopt_rig, G2's rig line, 0 points."""
    prof = _load(monkeypatch, "screen-planopt-rig")
    chains.check_gradeable(prof)
    got = {fs["name"]: fs for s in chains.sections(prof) for fs in s["flat"]}
    assert {n: (fs["count"], fs["item"]["band"]) for n, fs in got.items()} == {
        "planopt_rig_L1": (2, "low"), "planopt_rig_L2": (2, "mid"), "planopt_rig_L3": (2, "high"),
        "planopt_t1_L2": (4, "mid"), "planopt_t1_L3": (4, "high"), "planopt_t1_L4": (4, "top")}
    cases = {c["id"]: c for c in chains.chain_cases(prof)}
    assert len(cases) == 18
    for nm in ("planopt_t1_L2", "planopt_t1_L3", "planopt_t1_L4"):   # one turn, no history, own instances
        a = got.pop(nm)["item"]
        assert (a["ceiling"], a["ran_out"], a.get("history_budget")) == (16384, "exclude", None)
        m = chains.materialize(chains.instance(cases[f"FLAT-{nm}-001"], 1, 1, release_seed="4.5.0"))
        assert len(m["turns"]) == 1 and {tuple(c["turns"]) for c in m["checks"]} == {(1,)}
    for fs in got.values():
        a = fs["item"]
        assert (a["ceiling"], a["history_budget"], a["ran_out"], a["concept"]) == (16384, 49000, "exclude", "H4")
        assert a["ceiling"] + a["history_budget"] + 8192 <= 73728
    assert prof["scores"]["chat"]["parts"]["bk_plan"]["max"] == 0


def test_screen_headroom_ref_nod_is_the_full_screen_without_hr_d(monkeypatch):
    """screen-headroom-ref-nod (lead 2026-11-14): the launch-time cut of screen-headroom-ref -- every case of hr_a / hr_b /
    hr_c with the same id and the same instance (rows pool), nothing of hr_d."""
    full = _load(monkeypatch, "screen-headroom-ref")
    fcases = {c["id"]: c for c in chains.chain_cases(full)}
    finst = {i: chains.instance(c, 1, 1) for i, c in fcases.items()}
    core = _load(monkeypatch, "screen-headroom-ref-nod")
    ccases = {c["id"]: c for c in chains.chain_cases(core)}
    assert not any("hr_istack8" in i or "hr_scene7" in i for i in ccases)
    assert set(ccases) == {i for i in fcases if not ("hr_istack8" in i or "hr_scene7" in i)}
    assert len(ccases) == 4 + 3 + 6 + 6
    for i, c in ccases.items():
        a, b = chains.instance(c, 1, 1), finst[i]
        assert a.get("generator") == b.get("generator") and a.get("multi") == b.get("multi"), i
        assert a.get("generator") or a.get("multi"), i


def test_rc1_main_routing_and_packs(monkeypatch):
    """rc1-main (Admin G2, g2-3): one routed command; packs are 4.x ladder sets per part (Jake 2026-11-14, the published
    rule), never routing sets or max-0 chain parts; no effort line; every part has standard points."""
    prof = _load(monkeypatch, "rc1-main")
    sp = routing.spec(prof)
    assert len(sp["modules"]) == 3 and sp["modules"][-1]["unlock"] and not sp["modules"][-1]["packs"]
    names = {fs["name"].rsplit("_L", 1)[0] for s in chains.sections(prof) for fs in s["flat"]}
    assert not any("pack" in n or "probe" in n for n in names)
    assert {"rt_ledger_lo", "rt_ledger_mid", "rt_ledger_top", "rt_know_lo", "rt_know_mid", "rt_know_top",
            "rt_pytrap_top", "rt_scene_lo", "rt_scene_mid", "rt_scene_top"} <= names
    assert "effort_cells" not in prof["routing"]
    for sec, want in {"work": 10.0, "chat": 10.0}.items():
        parts = prof["scores"][sec]["parts"]
        assert all(float(p["max"]) > 0 for k, p in parts.items() if k not in ("h3_scene", "c3_mini", "c3_fix")), sec
        assert sum(float(p["max"]) for p in parts.values()) == want
    pyl = [fs["item"] for s in chains.sections(prof) for fs in s["flat"] if fs["item"]["family"] == "pytrap"]
    assert len(pyl) == 4 and all(i["level"] == 1 and i.get("ran_out", "zero") == "zero" and i["ceiling"] == 32768 for i in pyl)
    assert {k: p["unlock_from"] for sec in ("work", "chat") for k, p in prof["scores"][sec]["parts"].items()
            if p.get("unlock_from")} == {"c1_implied": "mid", "c2_trace": "low", "c5_tools": "low", "h6_know": "mid"}
    # H3 SCENESHIFT: measured at 0 points (lead 2026-11-14), the same 4 instances on every route
    import json
    assert float(prof["scores"]["chat"]["parts"]["h3_scene"]["max"]) == 0
    mods = {m["name"]: set(m["sets"]) for m in sp["modules"]}
    assert all(any(n.startswith(f"rt_scene_{t}_") for n in mods[m]) for m, t in (("low", "lo"), ("mid", "mid"),
                                                                                    ("top", "top"))), mods
    by_line = {}
    for c in chains.chain_cases(prof):
        if c["id"].startswith("FLAT-rt_scene_"):
            line = c["id"].split("FLAT-")[1].rsplit("_L", 1)[0]
            inst = chains.instance(c, 1, 1)
            g = {k: v for k, v in (inst.get("generator") or {}).items() if k != "seed_case"}   # mid/top seed on lo
            by_line.setdefault(line, []).append(json.dumps(g, sort_keys=True))
    assert set(by_line) == {"rt_scene_lo", "rt_scene_mid", "rt_scene_top"}
    assert len(by_line["rt_scene_lo"]) == 4
    assert by_line["rt_scene_lo"] == by_line["rt_scene_mid"] == by_line["rt_scene_top"]


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
def test_rc1_main_ladder_packs_reproduce_measured_instances(monkeypatch):
    """The pack set files reuse today's measured flat instances byte for byte (generator + seed_case), sit under
    rc1-main's own part keys (main-routed never sees them), and the Chat pack is staged until its rows land."""
    import copy, json, pathlib
    from crucibleforge import gen as _gen
    prof = _load(monkeypatch, "rc1-main")
    sets = ladder.discover(prof, staged=True)
    assert {k: [s["level"] for s in v] for k, v in sets.items() if k.split("/")[1][:1] in "ch" and "_" in k} == {
        "work/c1_implied": [1], "work/c2_trace": [1], "chat/h6_know": [1],
        "work/c5_tools": [1]}   # c2 set-2 closed (draft); c5 set-1 (PROBE) deleted; c5 set-2 = LAG1-2 (PACKS2, 2026-11-15)
    assert "chat/h6_know" not in ladder.discover(prof, staged=False)
    routed = _load(monkeypatch, "main-routed")
    assert not set(ladder.discover(routed, staged=True)) & {"work/c1_implied", "work/c2_trace", "work/c5_tools",
                                                             "chat/h6_know"}
    root = pathlib.Path(__file__).parents[1] / "cases" / "ladder"
    for f, comp in [("work/c1_implied/set-1.json", "pack-hardcode"), ("work/c2_trace/set-1.json", "pack-unknown"),
                    ("work/c2_trace/set-2.json", "pack-hardcode")]:   # c5 set-1 (PROBE) deleted 2026-11-15
        spec = json.loads((root / f).read_text(encoding="utf-8"))
        c = spec["cases"][0]
        sc = c["generator"]["seed_case"]
        src = _load(monkeypatch, comp)
        flat = {x["id"]: x for x in chains.chain_cases(src)}[sc.rsplit("-r1-a1", 1)[0]]
        inst = chains.instance(flat, 1, 1)
        assert inst["id"] == sc and inst["generator"]["key"] == c["generator"]["key"]
        assert _gen.materialize_case(copy.deepcopy(inst))["prompt"] == _gen.materialize_case(copy.deepcopy(c))["prompt"]


def test_rc1_main_implied_l1_l3_exclude_stopgap(monkeypatch):
    """IMPLIED L1/L3 run ran_out: exclude (stop-gap until two briefs per generation; lead 2026-11-14 12:3x)."""
    prof = _load(monkeypatch, "rc1-main")
    imp = [fs["item"] for s in chains.sections(prof) for fs in s["flat"] if fs["item"]["family"] == "implied"]
    assert imp and all(i["ran_out"] == "exclude" for i in imp)


def test_rc1_main_size_b_thins_on_the_same_seeds(monkeypatch):
    """Size B (lead 2026-11-14): mid / top thin the ceiling cells by lower counts on the same seeds -- every rc1-main case
    except the new patchmini lines is a g2-3 case with the same instance (fixture = g2-3's rc1-main instance hashes);
    the low route is untouched; patchmini L1 / L2 x4 are measured at 0 points, the same instances on every route, and
    the low sets keep the C3 screen's set names (their first two units are the stored screen instances)."""
    import hashlib, json, pathlib
    prof = _load(monkeypatch, "rc1-main")
    fx = json.loads((pathlib.Path(__file__).parent / "fixtures" / "rc1_main_g2_3_instances.json").read_text(encoding="utf-8"))

    def key(c):
        inst = chains.instance(c, 1, 1)
        g = {k: v for k, v in (inst.get("generator") or {}).items() if k != "seed_case"}
        return hashlib.sha256(json.dumps({"g": g, "m": inst.get("multi")}, sort_keys=True,
                                         default=str).encode()).hexdigest()[:16]
    cases = {c["id"]: c for c in chains.chain_cases(prof)}
    old = {i: c for i, c in cases.items() if "patchmini" not in i and "rt_mini" not in i}
    assert set(old) <= set(fx)
    assert all(key(c) == fx[i] for i, c in old.items())
    gone = set(fx) - set(old)
    assert {i.rsplit("_L", 1)[0] for i in gone} == {"FLAT-rt_attend_mid", "FLAT-rt_attend_top", "FLAT-rt_istack_mid",
                                                    "FLAT-rt_istack_top7", "FLAT-rt_state_mid", "FLAT-rt_state_top"}
    sp = routing.spec(prof)
    low = next(m for m in sp["modules"] if m["name"] == "low")
    assert not any(i.rsplit("_L", 1)[0][5:] in low["sets"] for i in gone)
    mini = {i: key(c) for i, c in cases.items() if "patchmini" in i or "rt_mini" in i}
    assert len(mini) == 24

    def line(prefix):
        return [mini[i] for i in sorted(mini) if i.startswith(prefix)]
    assert line("FLAT-patchmini_c3lo_") == line("FLAT-rt_mini1_mid_") == line("FLAT-rt_mini1_top_")
    assert line("FLAT-patchmini_c3mid_") == line("FLAT-rt_mini2_mid_") == line("FLAT-rt_mini2_top_")
    assert float(prof["scores"]["work"]["parts"]["c3_mini"]["max"]) == 0
    assert float(prof["scores"]["work"]["parts"]["c3_fix"]["max"]) == 0   # gameable until fixed (Jake 2026-11-14)
    scr = _load(monkeypatch, "screen-c3-ref")
    sc = {c["id"]: key(c) for c in chains.chain_cases(scr)}
    for i in ("FLAT-patchmini_c3lo_L1-001", "FLAT-patchmini_c3lo_L1-002", "FLAT-patchmini_c3mid_L2-001",
              "FLAT-patchmini_c3mid_L2-002"):
        assert mini[i] == sc[i], i


def test_rc1_main_calibration_covers_every_scored_cell(monkeypatch):
    """calibration-g2-3 (Admin G2; D's route-time failure on an uncalibrated locator cell): rc1-main names it, and it
    carries a difficulty for every locator cell and every module cell of a part with points, under scale.cell_of's
    names (``:x<n>`` for packed prompts). The 0-point measured lines stay UNcalibrated (no family trend either): a
    calibrated cell outside the form would be scored under ``<section>+packs``. Every calibrated cell is in the form."""
    from crucibleforge import scale
    prof = _load(monkeypatch, "rc1-main")
    sp = routing.spec(prof)
    assert prof["routing"]["calibration"] == "calibration/calibration-g2-3b.json"   # g2-3 minus patchfix:L5
    cal, _ = routing.calibration(sp, __import__("pathlib").Path(__file__).parents[1])
    assert not cal.get("families")
    zero = {f"{k.split('/')[0]}/{pk}" for k in ("work", "chat")
            for pk, p in (prof["scores"].get(k, {}).get("parts") or {}).items() if float(p.get("max") or 0) == 0}
    sets = set(sp["locator"]) | {s for m in sp["modules"] for s in m["sets"]}
    need, free = set(), set()
    for c in chains.chain_cases(prof):
        cs = c.get("chain_spec") or {}
        if cs.get("ladder") not in sets:
            continue
        n = len(cs.get("items") or [])
        part = next((k for k, v in (prof.get("chains") or {}).items()
                     if any(cs["ladder"] == fs.get("name") or cs["ladder"].startswith(f"{fs.get('name')}_L")
                            for fs in (v.get("flat") or []))), None)
        for rg in cs["rungs"]:
            cell = f"{rg['family']}:L{rg['level']}" + (f":x{n}" if n > 1 else "")
            (free if part in zero and cs["ladder"] not in sp["locator"] else need).add(cell)
    missing = sorted(c for c in need if scale.item(cal, c) is None)
    assert not missing, missing
    assert all(scale.item(cal, c) is None for c in free - need), sorted(free - need)
    assert {"sceneshift:L1"} <= free
    for sec, form in cal["form"].items():
        assert abs(sum(form.values()) - 1) < 1e-3
    assert {c for c in cal["cells"]} <= set(cal["form"]["work"]) | set(cal["form"]["chat"])
    assert scale.item(cal, "TZ16-pickup-dst-delay") is not None
    # K: _in_main drops a unit whose band is top (row block OR calibration cell): no form cell may carry band top
    assert not [c for c, v in cal["cells"].items() if isinstance(v, dict) and v.get("band") == "top"]
    assert not [fs["name"] for s in chains.sections(prof) for fs in s["flat"]
                if fs["item"].get("band") == "top" and fs["name"].rsplit("_L", 1)[0] in
                {x.rsplit("_L", 1)[0] for x in sets}]


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
def test_rc1_main_is_an_internal_candidate_labels_and_points_only(monkeypatch):
    """Jake 2026-11-14 review rulings (lead): rc1 is an internal candidate (board note); C3 = 0 points, measured only,
    Coding renormalised to 10 (C1 5.9 / C2 2.9 / C5 1.2) and patchfix:L5 uncalibrated; the planted_opt pack is closed.
    Labels and points only: every rc1-main case id and instance equals the tree tonight's rc1 rows are made on."""
    import hashlib, json, pathlib
    from crucibleforge import scale
    prof = _load(monkeypatch, "rc1-main")
    assert prof["board_notes"]["status"].startswith("internal candidate: the reference sits near the top of Main")
    for k in ("judged", "real-person", "scene-change", "planning", "packs are staged pending the owner's review"):
        assert k in prof["board_notes"]["status"]
    parts = prof["scores"]["work"]["parts"]
    assert {k: float(p["max"]) for k, p in parts.items()} == {"c1_implied": 5.9, "c2_trace": 2.9, "c5_tools": 1.2,
                                                              "c3_fix": 0.0, "c3_mini": 0.0}
    sp = routing.spec(prof)
    cal, _ = routing.calibration(sp, pathlib.Path(__file__).parents[1])
    assert scale.item(cal, "patchfix:L5") is None and "patchfix:L5" not in cal["form"]["work"]
    assert cal["form"]["work"]["pytrap:L1"] == 0.29 and cal["form"]["work"]["TZ16-pickup-dst-delay"] == 0.12
    g3 = json.loads((pathlib.Path(__file__).parents[1] / "calibration" / "calibration-g2-3.json").read_text(encoding="utf-8"))
    assert {c: v for c, v in g3["cells"].items() if c != "patchfix:L5"} == cal["cells"]
    s2 = json.loads((pathlib.Path(__file__).parents[1] / "cases/ladder/work/c2_trace/set-2.json").read_text(encoding="utf-8"))
    assert s2["draft"] is True and s2["note"].startswith("CLOSED")
    # PROBE (c5 set-1): its reference was too weak to anchor the ladder, so it failed verify; deleted rather
    # than kept as a closed draft (lead 2026-11-15)
    assert not (pathlib.Path(__file__).parents[1] / "cases/ladder/work/c5_tools/set-1.json").exists()
    assert [s["n"] for s in ladder.discover(prof, staged=True)["work/c2_trace"]] == [1]
    fx = json.loads((pathlib.Path(__file__).parent / "fixtures" / "rc1_main_rc1_tree_instances.json").read_text(encoding="utf-8"))

    def key(c):
        inst = chains.instance(c, 1, 1)
        g = {k: v for k, v in (inst.get("generator") or {}).items() if k != "seed_case"}
        return hashlib.sha256(json.dumps({"g": g, "m": inst.get("multi")}, sort_keys=True,
                                         default=str).encode()).hexdigest()[:16]
    assert {c["id"]: key(c) for c in chains.chain_cases(prof)} == fx["cases"]
    assert prof["cases"] == fx["other_cases"]
