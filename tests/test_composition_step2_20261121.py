"""Step 2 of CRITICAL-PATH-45 (composition): Fixes from generated levels with no token-allowance cliff (patchmini
L1-3, patchfix L1-4, patchmod L3), patchfix L5 and above in the Fixes pack, the seeded tool tasks in Tools (toolworld
L1-3, toolgap L1-2), the option-A repeats, the engine ladder packs opening on the top of the model's own range, the
NSFW+ craft pack's own stated time limit, and the board render no longer leaving the materialise cache on.

Counts come from the sizing re-run on the Fixes probe rows (BUILD-45.md "Composition (step 2)"). Points and weights of
the score tree are unchanged; inside Fixes today's family points stay (bug-hunt 1.2, patchfix + patchmini 1.2,
patchmod 0.6 of 3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from crucibleforge import accuracy45 as A
from crucibleforge import fixrows45, ladder
from crucibleforge import segments45 as S

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]

ROOT = Path(__file__).resolve().parents[1]
RAW = yaml.safe_load((ROOT / "profiles" / "bench45.yaml").read_text(encoding="utf-8"))
STD = RAW["compositions"]["standard"]
PROF = {**RAW, **STD}

SCORED_LEVELS = {("patchmini", 1): 2, ("patchmini", 2): 2, ("patchmini", 3): 2,
                 ("patchfix", 1): 1, ("patchfix", 2): 1, ("patchfix", 3): 1, ("patchfix", 4): 1,
                 ("patchmod", 3): 12}
BH = "CB-F1-larder-bughunt"


def _gen_ids():
    return [f"{f.upper()}-core-{lv}-{i}" for (f, lv), n in SCORED_LEVELS.items() for i in range(1, n + 1)]


# ------------------------------------------------------------------------------------------- Fixes composition
def test_fixes_are_generated_only_at_levels_without_the_allowance_cliff():
    got = {(e["family"], e["level"]): e["count"] for e in STD["core_generated"]}
    assert got == SCORED_LEVELS
    assert all(e["budget"] == 65536 and e["category"] == "coding" for e in STD["core_generated"])
    assert not any(e["family"] == "patchfix" and e["level"] >= 5 for e in STD["core_generated"])


def test_fixes_units_are_the_core_instances_and_the_bughunt():
    from crucibleforge import profiles
    fx = S.tree(PROF)["code"]["fixes"]
    ids = [c["id"] for c in profiles.core_generated_cases(PROF)]
    assert sorted(ids) == sorted(_gen_ids())
    assert sorted(fx["cases"]) == sorted(_gen_ids() + [BH])
    assert fx["case_repeats"] == {BH: 24}
    assert STD["case_repeats"][BH] == 24


def test_fixes_slices_keep_todays_family_points():
    fx = S.tree(PROF)["code"]["fixes"]
    assert {k: v["points"] for k, v in fx["slices"].items()} == {"bughunt": 1.2, "patch": 1.2, "patchmod": 0.6}
    assert sum(v["points"] for v in fx["slices"].values()) == pytest.approx(fx["max"]) == pytest.approx(3)
    # patchmini shares patchfix's slice
    assert fx["slices"]["patch"]["match"] == ["PATCHFIX-", "PATCHMINI-"]


def _fix_model(score_of):
    fx = S.tree(PROF)["code"]["fixes"]
    reps = fx["case_repeats"]
    return {"fix45": {"cases": {c: {"scores": [score_of(c)] * int(reps.get(c, 1)), "reason": None}
                                for c in fx["cases"]}}}


def test_fix_units_weigh_their_slice_points():
    fx = S.tree(PROF)["code"]["fixes"]
    g = S._segment(_fix_model(lambda c: 1.0), "fixes", fx, {})
    w = {}
    for u in g["units"]:
        w[u["id"]] = w.get(u["id"], 0.0) + u["weight"]
    assert g["points"] == pytest.approx(3.0)
    assert w[BH] == pytest.approx(1.2)
    assert sum(v for k, v in w.items() if k.startswith(("PATCHFIX-", "PATCHMINI-"))) == pytest.approx(1.2)
    assert sum(v for k, v in w.items() if k.startswith("PATCHMOD-")) == pytest.approx(0.6)
    g0 = S._segment(_fix_model(lambda c: 0.0 if c.startswith("PATCHMOD-") else 1.0), "fixes", fx, {})
    assert g0["points"] == pytest.approx(2.4)


def test_fix_range_uses_the_slice_weights():
    fx = S.tree(PROF)["code"]["fixes"]
    m = _fix_model(lambda c: 1.0)
    m["fix45"]["cases"][BH]["scores"] = [1.0, 0.0] * 12
    g = S._segment(m, "fixes", fx, {})
    st = A.settings(PROF)
    b = A._segment(m, "code", "fixes", g, fx, {}, st)
    s2 = sum((x - 0.5) ** 2 for x in [1.0, 0.0] * 12) / 23
    assert b["half"] == pytest.approx(A.Z90 * (1.2 ** 2 * s2 / 24) ** 0.5, abs=1e-4)


def test_patchmini_rows_are_graded_by_the_host_compared_checker():
    case = {"id": "PATCHMINI-core-1-1", "generator": {"type": "patchmini", "level": 1}, "grader_config": {"x": 1}}
    assert fixrows45.is_fix_case(case)
    assert "patchmini" in fixrows45.FIX_FAMILIES


# ------------------------------------------------------------------------------------------- the Fixes pack
#: live since pack questions render lazily (test_lazy_pack_render_20261121)
FIX_PACK = ROOT / "cases" / "ladder" / "work" / "c3_fix" / "set-3.json"


def test_patchfix_l5_and_above_are_the_fixes_pack():
    spec = json.loads(FIX_PACK.read_text(encoding="utf-8"))
    assert spec.get("status") == "live"
    assert spec["generator"] == {"family": "patchfix", "rungs": [5, 6, 7], "per_rung": 2}
    assert spec["points"] == 1.0 and spec["budget"] == 65536
    part = STD["scores"]["work"]["parts"]["c3_fix"]
    assert float(part["max"]) == 0 and part["pack_segment"] == "fixes"


def _det(std, sets):
    return {"std_share": std, "unlock_share": std, "max": 1.0,
            "sets": [{"n": n, "level": lv, "points": 1.0, "state": "locked", "share": sh} for n, lv, sh in sets]}


def test_fixes_pack_opens_on_the_top_of_the_fixes_range():
    from crucibleforge import enginepacks45 as E
    det = _det(None, [(3, 1, 0.5)])
    tb = {"segments": {"fixes": {"points": 2.6, "max": 3.0, "complete": True}}}
    lo = E.fixes_pack(det, tb, {"code/fixes": {"half": 0.05, "max": 3.0}}, 0.9)
    hi = E.fixes_pack(det, tb, {"code/fixes": {"half": 0.12, "max": 3.0}}, 0.9)
    assert lo["state"] == "locked" and lo["points"] == 0.0
    assert hi["state"] == "scored" and hi["points"] == pytest.approx(0.5)
    assert hi["gate_top"] == pytest.approx((2.6 + 0.12) / 3, abs=1e-4)
    held = {"segments": {"fixes": {"points": 2.9, "max": 3.0, "complete": False}}}
    assert E.fixes_pack(det, held, {}, 0.9)["state"] == "locked"          # a partly measured Fixes opens nothing


# ------------------------------------------------------------------------------------------- engine packs: top of range
def test_engine_pack_opens_on_the_top_of_the_parts_range():
    from crucibleforge import enginepacks45 as E
    det = _det(0.88, [(1, 1, 0.6), (2, 2, 0.4)])
    assert E.pack_points(det, 0.88, 0.9) == 0.0
    assert E.pack_points(det, 0.88 + 0.03, 0.9) == pytest.approx(0.6)      # level 2 needs level 1 >= 0.9 too
    card = {"work": {"c2_trace": det}}
    acc = {"code/reads": {"half": 0.03, "max": 1.0}}
    d = E.engine_delta(card, S.tree(PROF), acc, {}, 0.9)
    assert d["points"] == pytest.approx(0.6) and d["parts"]["work/c2_trace"]["gate_top"] == pytest.approx(0.91)
    # the part's own measured repeat range wins over its segment's when it has one
    d2 = E.engine_delta(card, S.tree(PROF), acc, {"work/c2_trace": 0.005}, 0.9)
    assert d2["points"] == 0.0


def test_engine_parts_map_to_their_segments():
    from crucibleforge import enginepacks45 as E
    m = E.part_segments(S.tree(PROF))
    assert m["work/c1_implied"] == "code/writes" and m["work/c5_tools"] == "code/tools"
    assert m["work/c2_trace"] == "code/reads" and m["chat/h6_know"] == "chat/everyday"
    assert m["work/c3_fix"] == "code/fixes"
    assert "chat/pk_character" not in m                   # Chat packs keep packs45's own gate


def test_run_side_gates_add_the_range():
    from crucibleforge import enginepacks45 as E
    out = {"work/c2_trace": {"std": 0.88}, "work/c3_fix": {"std": None}, "chat/pk_character": {"std": 0.7}}
    tb = {"code": {"segments": {"fixes": {"points": 2.6, "max": 3.0, "complete": True}}}}
    acc = {"code/reads": {"half": 0.03, "max": 1.0}, "code/fixes": {"half": 0.12, "max": 3.0}}
    E.run_gates(out, tb, acc, S.tree(PROF))
    assert out["work/c2_trace"]["std"] == pytest.approx(0.91)
    assert out["work/c3_fix"]["std"] == pytest.approx((2.6 + 0.12) / 3)
    assert out["chat/pk_character"]["std"] == 0.7            # not an engine part


# ------------------------------------------------------------------------------------------- Tools
def test_tools_gain_the_seeded_tool_tasks():
    ch = STD["chains"]["work/c5_toolgen"]
    fl = {e["family"]: {lv: (v["count"] if isinstance(v, dict) else v) for lv, v in e["levels"].items()}
          for e in ch["flat"]}
    assert fl == {"toolworld": {1: 6, 2: 6, 3: 6}, "toolgap": {1: 6, 2: 6}}
    assert ch["average"] == "unit"
    tools = S.tree(PROF)["code"]["tools"]
    assert tools["weights_are"] == "points" and tools["parts"] == {"c5_tools": 1.0, "c5_toolgen": 1.0}
    assert float(STD["scores"]["work"]["parts"]["c5_toolgen"]["max"]) == 0
    cr = STD["case_repeats"]
    assert (cr["TZ16-pickup-dst-delay"], cr["TZ19-hotel-cot-deadline"], cr["TZ20-swim-saturday-age-day"]) == (10, 4, 4)


# ------------------------------------------------------------------------------------------- the other option-A lines
def test_option_a_repeats():
    cr = STD["case_repeats"]
    assert cr["CB-C5-predict-pyhouse"] == 1 and cr["CB-C6-predict-node"] == 1 and cr["CB-C3-predict-sqlite"] == 1
    assert (cr["RD1-marisol"], cr["WR1-ninth-string"], cr["WR2-red-thread-key"]) == (2, 2, 6)
    assert STD["assistant"]["repeats"] == 1
    assert all(cr[c] == 1 for c in STD["assistant"]["cases"])


def test_long_scene_repeats_reach_the_run_as_data():
    from crucibleforge import profiles
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    per = p.get("case_repeats") or {}
    assert (per["RD1-marisol"], per["WR1-ninth-string"], per["WR2-red-thread-key"]) == (2, 2, 6)
    assert per[BH] == 24 and per["TZ16-pickup-dst-delay"] == 10


# ------------------------------------------------------------------------------------------- the craft pack's limit
def test_the_craft_pack_has_its_own_stated_limit(monkeypatch):
    from crucibleforge import estimate as est
    spec = json.loads((ROOT / "cases" / "ladder" / "nsfw" / "n6a_craft" / "set-1.json").read_text(encoding="utf-8"))
    assert spec["minutes_limit"] == 15
    monkeypatch.setattr(est, "pack_minutes", lambda *a, **k: 12.0)
    notes = ladder._time_warnings(spec, [], {}, {}, PROF)
    assert notes and not notes[0].startswith("WARNING") and "15" in notes[0]
    monkeypatch.setattr(est, "pack_minutes", lambda *a, **k: 16.0)
    assert ladder._time_warnings(spec, [], {}, {}, PROF)[0].startswith("WARNING")
    monkeypatch.setattr(est, "pack_minutes", lambda *a, **k: 6.0)
    assert ladder._time_warnings({}, [], {}, {}, PROF)[0].startswith("WARNING")        # the default 5 stays


def test_the_pack_builder_writes_the_craft_limit():
    src = (ROOT / "tools" / "build_pack_sets_nsfw45.py").read_text(encoding="utf-8")
    assert '"minutes_limit"' in src


# ------------------------------------------------------------------------------------------- cache leak (test_gen_fixb)
def test_report_command_leaves_the_materialise_cache_as_it_found_it(monkeypatch):
    from crucibleforge import cli, gen
    gen.enable_materialize_cache(False)

    class Args:
        check_stale = True
        models = ""
    monkeypatch.setattr(cli, "check_stale", lambda args, cfg: 0)
    assert cli.cmd_report(Args(), {"models": []}) == 0
    assert gen._MAT_CACHE_ON[0] is False
