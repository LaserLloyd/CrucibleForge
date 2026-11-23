"""rc2-main (Admin G2, lead 2026-11-14 evening): one fixed set for every model, no routing. Coding C1 5 (IMPLIED
L1:x3 x6) / C2 3 (TRACEVM L4, L6, L8 x3 each) / code prediction 2 (CB-C5 x5); Chat checked 3 (state L10 x3 1.5,
ledger L1 x3 1, KNOWGAP L3 x3 0.5) + the two judged parts (role-play 5, real person 2) declared and pending.
Every scored cell at what the 73,728 window allows after its largest measured prompt, ``ran_out: exclude``.
Fresh set names = fresh seeds (no rc2 instance equals any other composition's). On the non-routed path the pack
gate is the part's own share at ladder.unlock (0.9): the bonus, the margins and the pending part print."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import types
from pathlib import Path

import pytest

from crucibleforge import chains, cli, config, ladder, profiles, report, routing

_spec = importlib.util.spec_from_file_location("_tc", Path(__file__).with_name("test_chains_20261113.py"))
_tc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tc)
_toys = _tc._toys          # the toy families (autouse fixture of that module, used here too)

WINDOW = 73728


def _load(monkeypatch, comp):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, comp)
    return profiles.load_profile("bench45")


def _flat(prof):
    return {fs["name"]: (part, fs) for part, sec in ((s["part"], s) for s in chains.sections(prof))
            for fs in sec["flat"]}


def test_rc2_main_is_one_fixed_set_with_the_lead_s_points(monkeypatch):
    prof = _load(monkeypatch, "rc2-main")
    assert prof["composition"] == "rc2-main"
    assert "routing" not in prof and routing.spec(prof) is None and not report.routed(prof)
    assert prof["board_notes"]["status"] == ("internal candidate rc2: fixed set, no routing; "
                                             "Chat's judged parts (7 of 10 points) pending")
    assert prof["ladder"]["unlock"] == 0.9
    work, chat = prof["scores"]["work"]["parts"], prof["scores"]["chat"]["parts"]
    assert {k: float(p["max"]) for k, p in work.items()} == {"c1_implied": 5, "c2_trace": 3, "c2_predict": 2}
    assert {k: float(p["max"]) for k, p in chat.items()} == {"h2_state": 1.5, "h2_ledger": 1, "h6_know": 0.5,
                                                             "j_roleplay": 5, "j_person": 2}
    assert work["c2_predict"]["cases"] == ["CB-C5-predict-pyhouse"]
    # the judged parts are declared with no items: pending, the section scaled from the measured points
    assert not {"chat/j_roleplay", "chat/j_person"} & {s["part"] for s in chains.sections(prof)}
    assert (prof["cases"] or {}).get("codebank") == ["CB-C5-predict-pyhouse"]
    assert set(prof["cases"]["perf"]) == {"P1-tiny", "P2-short", "P3-gen", "P4-longctx"}
    sets = {n: (part, fs["item"]["family"], fs["item"]["level"], fs["count"], fs["n"])
            for n, (part, fs) in _flat(prof).items()}
    assert sets == {
        "rc2_implied1_L1": ("work/c1_implied", "implied", 1, 18, 3),   # 18 items = 6 prompts of 3
        "rc2_trace4_L4": ("work/c2_trace", "tracevm", 4, 3, 1),
        "rc2_trace6_L6": ("work/c2_trace", "tracevm", 6, 3, 1),
        "rc2_trace8_L8": ("work/c2_trace", "tracevm", 8, 3, 1),
        "rc2_state_L10": ("chat/h2_state", "session_state", 10, 3, 1),
        "rc2_ledger1_L1": ("chat/h2_ledger", "session_ledger", 1, 3, 1),
        "rc2_know3_L3": ("chat/h6_know", "knowgap", 3, 3, 1)}
    assert len(chains.chain_cases(prof)) == 6 + 3 * 6
    # the five adult-page Main cases ride along exactly as on every 4.5 composition
    assert prof.get("main_nsfw") == _load(monkeypatch, "rc1-main").get("main_nsfw")


def test_rc2_main_ceilings_fill_the_window_and_ran_out_excludes(monkeypatch):
    prof = _load(monkeypatch, "rc2-main")
    got = {}
    for c in chains.chain_cases(prof):
        cs = c["chain_spec"]
        rg = cs["rungs"][0]
        got.setdefault(cs["ladder"], (rg["ceiling"], rg["item_ceiling"], rg["ran_out"], len(cs.get("items") or [1])))
    assert got == {
        "rc2_implied1_L1": (65535, 21845, "exclude", 3),   # packed x3: the engine caps a packed prompt at 65,536
        "rc2_trace4_L4": (71680, 71680, "exclude", 1),     # 73,728 - 1,887 largest prompt, down to 1,024
        "rc2_trace6_L6": (70656, 70656, "exclude", 1),     # - 2,641
        "rc2_trace8_L8": (69632, 69632, "exclude", 1),     # - 3,213
        "rc2_state_L10": (16384, None, "exclude", 1),      # sessions keep their measured per-turn ceiling
        "rc2_ledger1_L1": (16384, None, "exclude", 1),
        "rc2_know3_L3": (71680, 71680, "exclude", 1)}      # - 1,521
    assert got["rc2_implied1_L1"][0] <= chains.MAX_PROMPT_CEILING
    assert all(v[0] < WINDOW for v in got.values())
    led = next(fs for _, fs in _flat(prof).values() if fs["name"] == "rc2_ledger1_L1")
    assert led["item"]["history_budget"] == 49000


def test_rc2_main_repeats_cb_c5_five_times_and_no_other_composition(monkeypatch):
    prof = _load(monkeypatch, "rc2-main")
    sel = {c["id"]: c for c in profiles._select(prof, {"codebank": prof["cases"]["codebank"]})}
    assert sel["CB-C5-predict-pyhouse"]["repeats"] == 5
    assert prof["case_repeats"]["CB-C3-predict-sqlite"] == 1          # merged onto the profile's, not replacing it
    for other in ("rc1-main", "main-v1"):
        o = _load(monkeypatch, other)
        assert "CB-C5-predict-pyhouse" not in (o.get("case_repeats") or {}), other
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "default")     # the base profile (unset = standard since the cut)
    assert "CB-C5-predict-pyhouse" not in (profiles.load_profile("bench45").get("case_repeats") or {})


def _key(c):
    inst = chains.instance(c, 1, 1)
    g = {k: v for k, v in (inst.get("generator") or {}).items() if k != "seed_case"}
    return hashlib.sha256(json.dumps({"g": g, "m": inst.get("multi")}, sort_keys=True,
                                     default=str).encode()).hexdigest()[:16]


def test_rc2_main_seeds_are_fresh(monkeypatch):
    """No rc2 instance equals an instance of any other composition (rc1 rows, calibration and screen sets), nor of a
    ladder pack: tonight's rows are an honest test of the predictions."""
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "default")     # the base profile (unset = standard since the cut)
    base = profiles.load_profile("bench45")
    rc2 = {c["id"]: _key(c) for c in chains.chain_cases(_load(monkeypatch, "rc2-main"))}
    assert len(set(rc2.values())) == len(rc2)
    seen = {_key(c) for c in chains.chain_cases(base)}
    for comp in sorted(base["compositions"]):
        # rc3-main, standard (was rc4-main) and rc4-main-ladder (equal to standard since the ladder switch, lead
        # 2026-11-15) reuse rc2 instances by design
        if comp not in ("rc2-main", "rc3-main", "standard", "rc4-main-ladder"):
            seen |= {_key(c) for c in chains.chain_cases(_load(monkeypatch, comp))}
    fx = json.loads((Path(__file__).parent / "fixtures" / "rc1_main_rc1_tree_instances.json").read_text(encoding="utf-8"))
    seen |= set(fx["cases"].values())
    assert len(seen) > 100
    assert not set(rc2.values()) & seen
    # nor a ladder pack's stored instance (the packs reuse rc1-era instances byte for byte)
    root = Path(__file__).parents[1] / "cases" / "ladder"
    gens = {json.dumps({k: v for k, v in c["generator"].items() if k != "seed_case"}, sort_keys=True)
            for f in root.glob("*/*/set-*.json")
            for c in json.loads(f.read_text(encoding="utf-8")).get("cases") or [] if isinstance(c, dict) and c.get("generator")}
    mine = {json.dumps({k: v for k, v in (chains.instance(c, 1, 1).get("generator") or {}).items()
                        if k != "seed_case"}, sort_keys=True)
            for c in chains.chain_cases(_load(monkeypatch, "rc2-main"))}
    assert gens and not mine & gens


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
def test_rc2_main_packs_sit_under_its_scored_parts(monkeypatch):
    prof = _load(monkeypatch, "rc2-main")
    run = ladder.discover(prof, staged=False)
    assert {k: [s["level"] for s in v] for k, v in run.items() if k.startswith(("work/c", "chat/h"))} == {
        "work/c1_implied": [1, 1], "work/c2_trace": [1],
        "work/c2_predict": [1], "chat/h6_know": [1]}   # c2_predict = the hard Predict trio (PACKS2 2026-11-14)
    # integration 2026-11-20: c1 set-2 = CQ1-3 and h6_know set-3 = EVERYGAP are live; Chat's KNOWGAP (set-1) stays staged
    assert [s["n"] for s in run["chat/h6_know"]] == [3]
    assert "chat/h6_know" in ladder.discover(prof, staged=True)
    assert report._ladder_sets(prof).keys() >= {"work/c1_implied", "work/c2_trace"}


# --- the non-routed scoring path, end to end on toy families (no model, no rig) ---------------------------------

def _toy_prof():
    """rc2-main's shape in miniature: two code-graded parts with a pack each, one judged part with no items."""
    prof = _tc._flatprof([{"family": "toychain", "level": 1, "count": 10, "name": "mn"}])
    prof["chains"]["work/aux"] = {"flat": [{"family": "toychain", "level": 1, "count": 10, "name": "ax"}]}
    prof["scores"]["work"]["parts"]["main"]["max"] = 5
    prof["scores"]["work"]["parts"]["aux"] = {"max": 3, "scoring": ["coding"], "cases": []}
    prof["scores"]["work"]["parts"]["judged"] = {"max": 2, "scoring": ["rpdeep"], "cases": []}
    prof["ladder"] = {"unlock": 0.9}
    prof["cases"] = {"coding": []}
    prof["scores"].pop("chat", None)
    return prof


def _drive(tmp_path, monkeypatch, pol):
    prof = _toy_prof()
    assert routing.spec(prof) is None
    root = tmp_path / "ladder"
    for part, tag in (("main", "PKM"), ("aux", "PKA")):
        d = root / "work" / part
        d.mkdir(parents=True)
        cases = [{"id": f"{tag}-{i}", "category": "coding",
                  "generator": {"type": "toychain", "level": 2, "key": f"{tag.lower()}{i}"}} for i in (1, 2)]
        (d / "set-1.json").write_text(json.dumps({"level": 1, "points": 1, "cases": cases}), encoding="utf-8")
    monkeypatch.setattr(ladder, "roots", lambda *a, **k: [root])
    monkeypatch.setattr(profiles, "load_profile", lambda name=None, cfg=None: prof)
    base_cfg = _tc._cfg
    monkeypatch.setattr(_tc, "_cfg", lambda p, **kw: {**base_cfg(p, **kw), "_profile": "t"})
    _tc._run(tmp_path, monkeypatch, prof, {"m": pol})
    cfg = _tc._cfg(prof)
    cfg["models"] = [{"name": "m", "model_id": "m", "provider": "p"}]
    args = types.SimpleNamespace(smoke=False, profile="t", no_tiers=False, no_ladder=False, no_judge=True,
                                 skip_conformance=True, escalate=False, judge=None, force_evict=False)
    monkeypatch.setattr(cli, "_escalate_phase", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_ProviderGuard", lambda *a, **k: types.SimpleNamespace(restore=lambda: None))
    assert cli._ladder_phases(args, cfg, ["m"]) == 0
    return cfg, {r["case_id"] for r in config.load_transcripts("m")}


def test_non_routed_gate_is_the_part_share_at_point_nine(tmp_path, monkeypatch):
    # main: 9 of 10 = 0.90 -> its pack runs; aux: 8 of 10 = 0.80 -> locked; the judged part has no item
    pol = lambda lv, cid: ("nope" if cid.startswith(("FLAT-mn-010", "FLAT-ax-009", "FLAT-ax-010"))   # noqa: E731
                           else "PASS")
    cfg, ran = _drive(tmp_path, monkeypatch, pol)
    assert {"PKM-1", "PKM-2"} <= ran and not {"PKA-1", "PKA-2"} & ran
    md = report.generate("m", write=False, cfg=cfg)
    # the bonus: main = 0.9 x 5 + 1 pack point; aux 0.8 x 3; judged pending; the section scaled and saying so
    # (the ± are the Scorecard's bootstrap margins, units resampled per part: test_headline_margin_20261114)
    assert re.search(r"\| m \| 9\.6 \(scaled from 7\.9 of 8 measured\) = Main 5\.5±[01]\.\d \(set 1\) MAXED "
                     r"\+ Aux 2\.4±0\.[2-9] \+ Judged pending \|", md), md
    assert '"pending" = no item yet (its category is scaled up from the measured points, stated in the cell)' in md
    assert "| m | locked | 100% |" in md                               # aux sets L1 | main sets L1
    assert "after ≥ 90%" in md
    # the margins print per part (the Scorecard's, one per part) with the analytic SE beside them
    assert re.search(r"\| m \| work/main \| 4\.50 / 5 \| 10/10 \| 10 \| - \| 0\.50 \| element \| [01]\.\d \|", md)
    assert re.search(r"\| m \| work/aux \| 2\.40 / 3 \| 10/10 \| 10 \| - \| 0\.40 \| element \| 0\.[2-9] \|", md)


def test_non_routed_gate_just_below_point_nine_stays_locked(tmp_path, monkeypatch):
    pol = lambda lv, cid: "nope" if cid.startswith(("FLAT-mn-009", "FLAT-mn-010")) else "PASS"   # noqa: E731
    cfg, ran = _drive(tmp_path, monkeypatch, pol)
    assert not {"PKM-1", "PKM-2"} & ran and {"PKA-1", "PKA-2"} <= ran   # main 0.8 locked; aux 1.0 opens


def test_rc3_main_spreads_predict_over_three_programs_and_keeps_rc2_instances(monkeypatch):
    """rc3-main (G2, lead 2026-11-14 late): Predict = CB-C5 x2 + CB-C6 x2 + CB-C3 x1 (19 graded questions, the same
    5 calls), its programs a bootstrap stratum; every generated cell is rc2's (same names, same instances)."""
    from crucibleforge import headline
    rc3, rc2 = _load(monkeypatch, "rc3-main"), _load(monkeypatch, "rc2-main")
    progs = ["CB-C5-predict-pyhouse", "CB-C6-predict-node", "CB-C3-predict-sqlite"]
    assert rc3["cases"]["codebank"] == progs
    assert rc3["scores"]["work"]["parts"]["c2_predict"]["cases"] == progs
    assert {c["id"]: c["repeats"] for c in profiles._select(rc3, {"codebank": progs})} == dict(zip(progs, [2, 2, 1]))
    assert headline.strata(rc3, report.scoring_config(None), None)[(1, "work/c2_predict")] == sorted(progs)
    k3 = {c["id"]: _key(c) for c in chains.chain_cases(rc3)}
    # every rc2 cell is in rc3 with the same instance; rc3 may add cells (N2 5b564b3: judged parts, adult page)
    k2 = {c["id"]: _key(c) for c in chains.chain_cases(rc2)}
    assert {i: k3.get(i) for i in k2} == k2
    # rc3 judged parts: N2 5b564b3; option C (G2 EXP-20261115-39): c5_tools added, c2_trace's points change only
    strip = lambda p: {k: v for k, v in p.items() if k not in ("c2_predict", "j_roleplay", "j_person", "c5_tools", "c2_trace")}   # noqa: E731
    for sec in ("work", "chat"):
        assert strip(rc3["scores"][sec]["parts"]) == strip(rc2["scores"][sec]["parts"])
    # c2_trace's points change, and it gains its public note (lead 2026-11-15): nothing else
    nomax = lambda d: {k: v for k, v in d.items() if k not in ("max", "note")}   # noqa: E731
    assert rc3["scores"]["work"]["parts"]["c2_trace"]["note"] == "scored by prefix: credit up to the first wrong line"
    assert nomax(rc3["scores"]["work"]["parts"]["c2_trace"]) == nomax(rc2["scores"]["work"]["parts"]["c2_trace"])
