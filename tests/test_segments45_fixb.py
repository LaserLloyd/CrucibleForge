"""Fix pass B: score tree arithmetic (M3), Overall without packs (S11), margins (N9), mark enforcement (M5)."""
from pathlib import Path

import pytest
import yaml

from crucibleforge import segments45 as S

_RAW = yaml.safe_load((Path(__file__).resolve().parent.parent / "profiles" / "bench45.yaml").read_text(encoding="utf-8"))
PROF = {**_RAW, **_RAW["compositions"]["standard"]}


def _model(share=0.5, pack=0.0, ladder=True):
    """A report block whose headline P carries ``pack`` extra points on the tools and predict parts."""
    parts = {"work/c5_tools": 1.0, "work/c5_toolgen": 1.0, "work/c2_predict": 0.5}   # step 2: Tools = 1 + 1
    hl = {"max": {k: v for k, v in parts.items()},
          "P": {k: share * v + pack for k, v in parts.items()}, "m": {}}
    m = {"headline": hl, "chain_stats": {}, "rpdeep": {"per_case": {}}, "assistant45": {}}
    if ladder:
        m["scorecard"] = {"ladder": {"work": {k.split("/")[1]: {"std_share": share, "max": v}
                                              for k, v in parts.items()}}}
    return m


def test_m3_tools_share_ignores_pack_points():
    tb = S.segment_table(_model(share=0.5, pack=0.8), PROF)
    units = {u["id"]: u for u in tb["code"]["segments"]["tools"]["units"]}
    assert units["c5_tools"]["share"] == 0.5               # was min(1, (1.0 + 0.8) / 2.0) = 0.9
    assert tb["code"]["segments"]["tools"]["points"] == pytest.approx(1.0)


def test_m3_share_above_one_is_an_error_not_clipped():
    m = _model(share=0.5)
    m["scorecard"]["ladder"]["work"]["c5_tools"]["std_share"] = 1.2
    with pytest.raises(ValueError):
        S.segment_table(m, PROF)


def test_m3_no_standard_share_is_pending_never_the_headline():
    tb = S.segment_table(_model(share=0.5, pack=0.8, ladder=False), PROF)
    u = {x["unit"]: x for x in tb["code"]["segments"]["tools"]["pending"]}
    assert "c5_tools" in u


# ---------------------------------------------------------------- M5: mark enforcement
import json


def _full_model():
    from test_segments45_sum import _model as _full
    m = _full(0.5)
    scenes = ("RV1-three-at-the-table", "RV2-skarren-hut", "CC3-tea-hut", "CC4-second-pitch", "RD1-marisol",
              "RD4-kettle-saddle", "WR1-ninth-string", "WR2-red-thread-key")
    m["rplong45"] = {"scenes": {c: {"score": 0.6, "reason": None} for c in scenes}}
    from test_segments45_sum import FIXES, FIX_REPS           # step 2: the declared Fixes units and repeats
    m["fix45"] = {"cases": {c: {"scores": [0.5] * int(FIX_REPS.get(c, 1)), "reason": None} for c in FIXES}}
    return m


def test_m5_every_segment_names_its_governing_marks():
    gov = S.SEGMENT_MARKS
    for key in ("code/writes", "code/fixes", "code/tools", "code/reads", "chat/character", "chat/remembers",
                "chat/cowrites", "chat/everyday", "nsfw/erotica", "nsfw/roleplay", "nsfw/range", "nsfw/anatomy",
                "unc/willingness", "unc/knowledge"):
        assert gov[key], key
    assert "chat_rplong" in gov["chat/remembers"]
    # Part 1 (2026-11-21): the reader marks C1-C6 hold no segment (one local judge classes the adult page)
    assert not any(x.startswith("classing:") for x in gov["nsfw/erotica"])
    assert any(x.startswith("act_beat:") for x in gov["nsfw/erotica"])


def test_m5_missing_marks_keep_the_number_and_say_so(tmp_path):
    """Part 2 item 10 (owner 2026-11-21): no mark sets held or pending; a missing mark prints as a note."""
    marks = S.load_marks(tmp_path)                                 # an empty folder: nothing applied
    tb = S.segment_table(_full_model(), PROF)
    S.apply_marks(tb, marks)
    w = tb["code"]["segments"]["writes"]
    assert w["points"] is not None and w["complete"]
    assert tb["overall"]["points"] is not None


def test_m5_a_failed_mark_is_a_note_and_moves_no_number(tmp_path):
    allpass = {"segments": {k: {"status": "pass"} for k in ("code/writes", "code/fixes", "code/tools", "code/reads",
                                                             "chat/character", "chat/remembers", "chat/cowrites",
                                                             "chat/everyday")},
               "chat_rplong": {"pass": True, "measured": True},
               "everyday": {f"EV{i}_x": {"status": "pass"} for i in range(1, 6)}}
    (tmp_path / "marks45.json").write_text(json.dumps(allpass), encoding="utf-8")
    (tmp_path / "assistant_marks.json").write_text(json.dumps({"ok": True, "reasons": []}), encoding="utf-8")
    tb = S.segment_table(_full_model(), PROF)
    S.apply_marks(tb, S.load_marks(tmp_path))
    assert tb["overall"]["complete"] and tb["overall"]["points"] is not None
    assert tb["code"]["segments"]["writes"]["marks"]["state"] == "applied"
    before = tb["chat"]["segments"]["remembers"]["points"]
    allpass["chat_rplong"] = {"pass": False, "measured": True, "reason": "judge term 6.1% of the part > 5%"}
    (tmp_path / "marks45.json").write_text(json.dumps(allpass), encoding="utf-8")
    tb = S.segment_table(_full_model(), PROF)
    S.apply_marks(tb, S.load_marks(tmp_path))
    rem = tb["chat"]["segments"]["remembers"]
    assert rem["points"] == before and tb["overall"]["points"] is not None


def test_m5_assistant_marks_failure_pends_everyday(tmp_path):
    (tmp_path / "assistant_marks.json").write_text(json.dumps({"ok": False, "reasons": ["cut-share mark failed"]}),
                                                   encoding="utf-8")
    marks = S.load_marks(tmp_path)
    assert marks["assistant"]["status"] == "fail" and "cut-share" in marks["assistant"]["reason"]


# ---------------------------------------------------------------- N9: margins include scenes, fixes, Everyday
def test_n9_segment_margins_include_scene_fix_and_everyday_parts():
    m = _full_model()
    m["assistant45"] = {"status": "ok", "share": 0.5, "margin": 10.0, "n_items": 15}   # stamp-2 G4/R2: the binomial bound reads n_items
    tb = S.segment_table(m, PROF)
    assert tb["chat"]["segments"]["cowrites"]["margin"] and tb["chat"]["segments"]["cowrites"]["margin"] > 0
    assert tb["code"]["segments"]["fixes"]["margin"] > 0
    assert tb["chat"]["segments"]["everyday"]["margin"] > 0
    # a certain unit (share 0 or 1) adds nothing, and its computed 0 is a stored margin (stamp-2 G4/R3; was None)
    m["rplong45"]["scenes"]["WR1-ninth-string"]["score"] = 1.0
    m["rplong45"]["scenes"]["WR2-red-thread-key"]["score"] = 1.0
    assert S.segment_table(m, PROF)["chat"]["segments"]["cowrites"]["margin"] == 0.0


# ---------------------------------------------------------------- S3: scorer stamp
def test_s3_scorer_stamp_covers_the_scorers_and_the_marks_index(tmp_path, monkeypatch):
    here = Path(S.__file__).resolve().parent
    for f in S.SCORER_FILES:
        assert (here / f).is_file(), f
    assert set(S.SCORER_FILES) >= {"nsfwplus45.py", "classing45.py", "actkeys45.py", "segments45.py",
                                   "rplong_mean.py", "attempts45.py", "fixrows45.py"}
    S.scorer_stamp.cache_clear()
    a = S.scorer_stamp()
    assert len(a) == 16 and a == S.scorer_stamp()
    # a different marks index gives a different stamp
    fake = tmp_path / "pkg"
    (fake / "calibration").mkdir(parents=True)
    for f in S.SCORER_FILES:
        (fake / f).write_bytes((here / f).read_bytes())
    (fake / "calibration" / "MARKS-INDEX-45.md").write_text("changed", encoding="utf-8")
    monkeypatch.setattr(S, "__file__", str(fake / "segments45.py"))
    S.scorer_stamp.cache_clear()
    assert S.scorer_stamp() != a
    S.scorer_stamp.cache_clear()


def test_m5_governing_marks_are_described_from_the_mark_files():
    seen = set()
    for ids in S.SEGMENT_MARKS.values():
        seen.update(ids)
    for mid in sorted(seen):
        d = S.describe(mid)
        assert d and d != mid, mid
    assert "known order" in S.describe("segment:code/writes")
    assert "constructed" in S.describe("classing:C1").lower() or "every reader" in S.describe("classing:C1")
    assert "reference" in S.describe("everyday:EV1").lower()
