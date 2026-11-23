"""Score tree: segments sum to Code and Chat, Overall = Code + Chat, weights match the spec shares (1.6)."""
from pathlib import Path

import yaml

from crucibleforge import segments45 as S

_RAW = yaml.safe_load((Path(__file__).resolve().parent.parent / "profiles" / "bench45.yaml").read_text(encoding="utf-8"))
PROF = {**_RAW, **_RAW["compositions"]["standard"]}   # the composition-merged view the report sees
FULL = {"code": 10, "chat": 10}
SHARES = {"code": {"writes": 4, "fixes": 3, "tools": 2, "reads": 1},
          "chat": {"character": 4, "remembers": 3.5, "cowrites": 1.5, "everyday": 1}}


def _model(share=0.5, scenes=True, basics=True):
    parts = ["c1_implied", "c2_predict", "c2_trace", "c5_tools", "c5_toolgen", "h2_state", "h2_ledger", "h6_know",
             "j_rpsession"]
    sect = {"c1_implied": "work", "c2_predict": "work", "c2_trace": "work", "c5_tools": "work", "c5_toolgen": "work"}
    mx = {f"{sect.get(p, 'chat')}/{p}": 1.0 for p in parts}
    m = {"headline": {"max": mx, "P": {k: share for k in mx}, "m": {}},
         "chain_stats": {}, "rpdeep": {"per_case": {}}, "assistant45": {},
         "scorecard": {"ladder": {k.split("/")[0]: {kk.split("/")[1]: {"std_share": share} for kk in mx
                                                    if kk.split("/")[0] == k.split("/")[0]} for k in mx}}}
    if basics:
        m["assistant45"] = {"status": "ok", "share": share}
    return m


def test_declared_maxima_match_spec():
    t = S.tree(PROF)
    for sec, segs in SHARES.items():
        assert {k: v["max"] for k, v in t[sec].items() if isinstance(v, dict) and "max" in v} == segs
        assert sum(segs.values()) == FULL[sec]


def test_sections_sum_and_overall():
    tb = S.segment_table(_model(0.5), PROF)
    for sec in ("code", "chat"):
        assert abs(tb[sec]["points"] - sum(g["points"] for g in tb[sec]["segments"].values() if g["points"] is not None)) < 1e-9
    assert abs(tb["overall"]["measured_points"] - tb["code"]["points"] - tb["chat"]["points"]) < 1e-9


def test_missing_scorers_are_pending_never_zero():
    tb = S.segment_table(_model(1.0), PROF)
    fixes = tb["code"]["segments"]["fixes"]
    # step 2: 24 bug-hunt repeats + 22 generated units
    assert fixes["points"] is None and fixes["pending_points"] == 3 and len(fixes["pending"]) == 46
    assert all(p["reason"].startswith("pending:") for p in fixes["pending"])
    assert tb["chat"]["segments"]["cowrites"]["points"] is None
    assert tb["overall"]["points"] is None          # no Overall until every segment reports
    assert tb["overall"]["pending_reasons"]


def test_everyday_moves_into_chat_and_pends_without_basics():
    tb = S.segment_table(_model(1.0, basics=False), PROF)
    ev = tb["chat"]["segments"]["everyday"]
    assert ev["points"] == 0.5 and ev["pending"][0]["unit"] == "assistant"


SCENES8 = ("RV1-three-at-the-table", "RV2-skarren-hut", "CC3-tea-hut", "CC4-second-pitch", "RD1-marisol",
           "RD4-kettle-saddle", "WR1-ninth-string", "WR2-red-thread-key")
FIXES = tuple(S.tree(PROF)["code"]["fixes"]["cases"])            # step 2: the bug-hunt + the 22 generated units
FIX_REPS = S.tree(PROF)["code"]["fixes"]["case_repeats"]


def test_overall_is_code_plus_chat_when_every_scorer_reports():
    m = _model(0.5)
    m["rplong45"] = {"scenes": {c: {"score": 0.6, "reason": None} for c in SCENES8}}
    m["fix45"] = {"cases": {c: {"scores": [0.5] * int(FIX_REPS.get(c, 1)), "reason": None} for c in FIXES}}
    tb = S.segment_table(m, PROF)
    assert tb["overall"]["complete"] and tb["overall"]["points"] is not None
    assert abs(tb["overall"]["points"] - (tb["code"]["points"] + tb["chat"]["points"])) < 1e-9
    # hand sum: writes .5*4 + fixes .5*3 + tools .5*2 + reads .5*1 = 5; chat: character (3*.5 + 1*.6) + remembers
    # (4 scenes*.5*.6 + 1*.5 + .5*.5) + cowrites 1.5*.6 + everyday (.5*.5+.5*.5)
    assert abs(tb["code"]["points"] - 5.0) < 1e-9
    assert abs(tb["chat"]["points"] - ((1.5 + 0.6) + (1.2 + 0.5 + 0.25) + 0.9 + 0.5)) < 1e-9


def test_inputs_absent_or_partial_stay_pending():
    m = _model(0.5)
    m["rplong45"] = {"scenes": {c: {"score": None, "reason": "pending: not read by a second reader"} for c in SCENES8}}
    m["fix45"] = {"cases": {"CB-F1-larder-bughunt": {"scores": [1.0], "reason": "pending: repeat 2 has no fix row yet"}}}
    tb = S.segment_table(m, PROF)
    fx = tb["code"]["segments"]["fixes"]
    # one CB-F1 unit of its 24 measured: the bug-hunt slice's 1.2 / 24 * 1.0 (step 2 slices)
    assert abs(fx["points"] - 0.05) < 1e-9 and len(fx["pending"]) == 45
    assert {p["reason"] for p in fx["pending"]} == {"pending: repeat 2 has no fix row yet", "pending: no fix rows yet"}
    assert tb["chat"]["segments"]["cowrites"]["points"] is None
    assert tb["chat"]["segments"]["cowrites"]["pending"][0]["reason"] == "pending: not read by a second reader"


def test_cowrites_are_three_quarter_points_each_and_match_the_scene_table():
    from crucibleforge import rplong_mean as RM
    cw = S.tree(PROF)["chat"]["cowrites"]
    assert cw["weights_are"] == "points" and cw["parts"]["rplong"] == {"WR1-ninth-string": 0.75, "WR2-red-thread-key": 0.75}
    m = _model(0.5)
    m["rplong45"] = {"scenes": {c: {"score": 1.0, "reason": None} for c in SCENES8}}
    units = S.segment_table(m, PROF)["chat"]["segments"]["cowrites"]["units"]
    assert [(u["id"], u["weight"]) for u in units] == [("WR1-ninth-string", 0.75), ("WR2-red-thread-key", 0.75)]
    assert RM.SCENES["WR1-ninth-string"] == ("cowrites", 0.75) == (RM.SCENES["WR2-red-thread-key"][0], 0.75)


def test_ladder_packs_never_enter_the_standard_twenty():
    tb = S.segment_table(_model(1.0), PROF)
    assert tb["code"]["max"] == 10 and tb["chat"]["max"] == 10 and tb["overall"]["max"] == 20
