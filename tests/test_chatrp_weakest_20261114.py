"""Role-play scored by the weakest live mark (CHAT-RP, lead 2026-11-14 night; Admin N2's stretch note
EXP-20261114-35 variant (d)). Report-time only: the stored verdicts are re-read, nothing is re-run.

A piece's share is its LOWEST mark among the live marks its own rubric asks for: ``judge_mode.axes`` lists
every mark a role-play card may ask (rp_single asks atmosphere, rp_multi consistency), ``dead_axes`` names the
marks the profile declares dead (4+ of the 6 models average 9.5+: character, agency, consistency), and
``combine: weakest`` takes the minimum of what is left that the verdict carries. Two seeds are still averaged
inside a card and the card is the bootstrap unit.

Fixture: tonight's rc3 role-play rows of the six models (results-rc2-*, deduped), replies replaced by a
placeholder, judge notes dropped (scores, policy and chain kept)."""
import json
import random
from pathlib import Path

import pytest

from crucibleforge import chains, headline, profiles
from crucibleforge.config import ConfigError

FIX = Path(__file__).parent / "fixtures" / "rc3_roleplay_rows_20261114.json"
#: EXP-20261114-35 CHAT-JUDGED-STRETCH.md table 1, variant (d), points of 5
WANT = {"sonnet": 4.03, "ref": 3.86, "dsflash": 3.78, "mm": 3.67, "grok": 3.03, "ds8b": 2.53}


def _prof(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "rc3-main")
    return profiles.load_profile("bench45")


def test_rc3_main_roleplay_is_weakest_live_mark_with_a_profile_dead_list(monkeypatch):
    p = _prof(monkeypatch)
    jm = {s["part"]: s for s in chains.sections(p)}["chat/j_roleplay"]["judge_mode"]
    assert jm["combine"] == "weakest"
    assert jm["dead_axes"] == ["character", "agency", "consistency"]
    assert set(jm["axes"]) == {"prose", "character", "dialogue", "emotion", "atmosphere", "agency", "consistency"}
    # real person keeps the plain mean of its one mark
    pj = {s["part"]: s for s in chains.sections(p)}["chat/j_person"]["judge_mode"]
    assert pj.get("combine", "mean") == "mean" and not pj.get("dead_axes")


@pytest.mark.parametrize("model", list(WANT))
def test_weakest_live_mark_reproduces_the_stretch_note(monkeypatch, model):
    p = _prof(monkeypatch)
    rows = json.loads(FIX.read_text(encoding="utf-8"))[model]
    st = chains.section_stats(rows, p)["chat/j_roleplay"]
    assert st["complete"] and st["max"] == 5.0
    assert st["unit_classes"]["judged"] == 18 and st["unit_classes"]["pending"] == 0
    assert round(st["points"], 2) == WANT[model]


def test_order_matches_the_stretch_note(monkeypatch):
    p = _prof(monkeypatch)
    d = json.loads(FIX.read_text(encoding="utf-8"))
    pts = {m: chains.section_stats(d[m], p)["chat/j_roleplay"]["points"] for m in WANT}
    assert sorted(pts, key=lambda m: -pts[m]) == list(WANT)


def _boot_margin(rows, p, it=600, seed=11):
    """90% half-width of the card-level bootstrap (headline strata: one id per card, both seeds follow)."""
    ids = headline.strata(p, {}, None)[(1, "chat/j_roleplay@units")]
    assert len(ids) == 9
    rnd = random.Random(seed)
    xs = []
    for _ in range(it):
        draw = {}
        for _i in ids:
            k = rnd.choice(ids)
            draw[k] = draw.get(k, 0) + 1
        xs.append(chains.section_stats(rows, p, draw)["chat/j_roleplay"]["points"])
    xs.sort()
    return (xs[int(.95 * it)] - xs[int(.05 * it)]) / 2


def test_margins_come_from_the_card_bootstrap(monkeypatch):
    """Stretch note: about +-.24-.29 for every model under (d) (+-.13-.25 under today's mean)."""
    p = _prof(monkeypatch)
    d = json.loads(FIX.read_text(encoding="utf-8"))
    for m in ("ref", "ds8b"):
        mg = _boot_margin(d[m], p)
        assert 0.15 < mg < 0.40, (m, mg)


# ------------------------------------------------------------------------------------ config + unit rules
def _sec(jm):
    return {"name": "t", "release_seed": "r", "cases": {"rpdeep": []},
            "scores": {"chat": {"label": "Chat", "parts": {"j": {"label": "J", "max": 5, "scoring": ["rpdeep"],
                                                                  "cases": []}}}},
            "chains": {"chat/j": {"judged": True, "judge_mode": jm, "average": "unit", "se_unit": "prompt",
                                  "flat": [{"name": "t_s1", "family": "jcard", "variant": "roleplay", "level": 1,
                                            "count": 2, "deal": True, "element": "check", "gen_seed": 1}]}}}


def test_judge_mode_weakest_config_rules():
    ok = {"kind": "rubric", "axes": ["prose", "emotion", "agency"], "dead_axes": ["agency"], "combine": "weakest"}
    jm = chains.sections(_sec(ok))[0]["judge_mode"]
    assert jm["combine"] == "weakest" and jm["dead_axes"] == ["agency"]
    with pytest.raises(ConfigError, match="combine"):
        chains.sections(_sec({**ok, "combine": "median"}))
    with pytest.raises(ConfigError, match="dead_axes"):
        chains.sections(_sec({**ok, "dead_axes": ["voice"]}))          # not one of axes
    with pytest.raises(ConfigError, match="dead_axes"):
        chains.sections(_sec({**ok, "dead_axes": ["prose", "emotion", "agency"]}))   # nothing live
    # the old shape is unchanged: mean, no dead list
    jm = chains.sections(_sec({"kind": "rubric", "axes": ["prose"]}))[0]["judge_mode"]
    assert jm.get("combine", "mean") == "mean" and jm.get("dead_axes", []) == []


def test_rubric_element_weakest_takes_the_lowest_live_mark_the_verdict_carries():
    jm = {"kind": "rubric", "axes": ["prose", "emotion", "atmosphere", "consistency", "agency"],
          "dead_axes": ["agency", "consistency"], "sexual_axes": [], "combine": "weakest"}
    blk = {"id": "U"}
    # an rp_single verdict: atmosphere asked, consistency not; agency (2) is dead and never counts
    el = chains.rubric_element(blk, {"scores": {"prose": 9, "emotion": 7, "atmosphere": 8, "agency": 2}}, {}, jm)
    assert el["score"] == pytest.approx(0.7) and el["axes"] == ["prose", "emotion", "atmosphere"]
    # an rp_multi verdict: no atmosphere asked -> prose, emotion
    el = chains.rubric_element(blk, {"scores": {"prose": 6, "emotion": 8, "consistency": 1}}, {}, jm)
    assert el["score"] == pytest.approx(0.6)
    # no live mark at all = a judge failure (pending), never a free score
    assert chains.rubric_element(blk, {"scores": {"agency": 9}}, {}, jm) is None
    # refused still scores 0
    el = chains.rubric_element(blk, {"scores": {"prose": 9, "emotion": 9, "refused": True}}, {}, jm)
    assert el["score"] == 0.0 and el["outcome"] == "refused"
    # mean mode keeps its rule: every listed axis must be on the verdict
    mean = {"kind": "rubric", "axes": ["prose", "atmosphere"], "sexual_axes": []}
    assert chains.rubric_element(blk, {"scores": {"prose": 9}}, {}, mean) is None
