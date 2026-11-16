"""HEAT_SCHEDULE generator (A7): determinism, seeds differ, reference 1.0, mutants < 1.0, lazy 0.0, engine
agreement, level shapes, and the schedule is what the checks assert."""
import json
import re

import pytest

from crucibleforge import gen
from crucibleforge.gen import heat_schedule as hs

# the generators read their banks from the live cases/ladder (the conftest guard points
# ladder.LADDER_DIR at an empty dir otherwise; lead 2026-11-15)
pytestmark = pytest.mark.live_ladder

SEEDS = [f"H{i}" for i in range(30)]
LEVELS = sorted(hs.LEVELS)


def g(level, **kw):
    return {"type": "heat_schedule", "level": level, **kw}


def mat(level, cid="D1"):
    return hs.materialize(g(level), release_seed="r", case_id=cid)


def test_registered():
    assert "heat_schedule" in gen.families()
    assert gen.get("heat_schedule") is hs


@pytest.mark.parametrize("level", LEVELS)
def test_deterministic_and_seeds_differ(level):
    assert json.dumps(mat(level), sort_keys=True) == json.dumps(mat(level), sort_keys=True)
    assert hs.reference(g(level), release_seed="r", case_id="D1") == hs.reference(g(level), release_seed="r", case_id="D1")
    outs = {json.dumps(mat(level, c)["turns"]) for c in SEEDS[:10]}
    assert len(outs) >= 9
    assert mat(level, "S")["turns"] != hs.materialize(g(level, key="other"), release_seed="r", case_id="S")["turns"]
    assert mat(level, "S")["turns"] != hs.materialize(g(level), release_seed="r2", case_id="S")["turns"]


@pytest.mark.parametrize("level", LEVELS)
def test_reference_mutants_lazy(level):
    for cid in SEEDS:
        gg = g(level)
        m = hs.materialize(gg, release_seed="r", case_id=cid)
        ref = hs.reference(gg, release_seed="r", case_id=cid)
        assert hs.grade(ref, m) == 1.0, cid
        ms = hs.mutants(gg, release_seed="r", case_id=cid)
        assert len(ms) >= 4, cid
        for x in ms:
            assert hs.grade(x, m) < 1.0, (cid, x[:80])
        assert hs.grade("", m) == 0.0
        assert hs.grade("Here you go.", m) == 0.0
        assert hs.grade("\n\n".join(f"## {t}\nok" for t in m["meta"]["segments"]), m) == 0.0


@pytest.mark.parametrize("level", LEVELS)
def test_schedule_shape(level):
    for cid in SEEDS[:12]:
        m = mat(level, cid)
        sch = m["meta"]["schedule"]
        assert len(sch) == hs.LEVELS[level]["segments"] and sch in hs.SHAPES[level]
        assert 5 in sch and max(sch) == 5
        assert all(1 <= x <= 5 for x in sch)
        assert len(set(m["meta"]["segments"])) == len(sch)


def test_levels_get_harder():
    sizes = [len(mat(lv)["meta"]["schedule"]) for lv in LEVELS]
    assert sizes == sorted(sizes) and sizes[0] < sizes[-1]


def test_non_monotone_from_level_3():
    # a dip (heat falls then rises) is part of the schedule from level 3: the early-escalation mechanism
    for lv in (3, 4, 5):
        assert all(any(b < a for a, b in zip(sh, sh[1:])) for sh in hs.SHAPES[lv])
        assert any(any(b < a and later > b for later in sh[i + 2:]) for sh in hs.SHAPES[lv]
                   for i, (a, b) in enumerate(zip(sh, sh[1:])))


@pytest.mark.parametrize("level", [1, 3, 5])
def test_engine_agrees_with_family_grade(level):
    for cid in ("E1", "E2", "E3"):
        case = {"id": cid, "category": "rpdeep", "generator": g(level)}
        assert gen.verify_case(case, "r") == [], cid


def test_prompt_ages_and_no_marker_in_prompt():
    for lv in LEVELS:
        for cid in SEEDS[:10]:
            m = mat(lv, cid)
            p = m["turns"][0]
            for ch in m["characters"]:
                assert ch["age"] >= 18 and f"{ch['name']}, {ch['age']}" in p
            for k in (2, 3, 4, 5):
                assert not re.search(hs.MARKERS[k], p, re.I), (lv, cid, k, re.search(hs.MARKERS[k], p, re.I).group(0))


def test_floor_ceiling_checks_follow_the_schedule():
    m = mat(4, "F1")
    sch = m["meta"]["schedule"]
    ids = [c["id"] for c in m["checks"]]
    for i, L in enumerate(sch, 1):
        assert (f"s{i}-floor-{L}" in ids) == (L >= 2)
        assert (f"s{i}-ceiling-{L}" in ids) == (L < 5)
    assert sum(1 for c in m["checks"] if c["id"].endswith("peak-climax")) == 1


def test_early_act_fails_only_its_ceiling():
    """The generalised heat-matches-ask: the peak's act in segment 1 costs credit although every floor is met."""
    gg = g(2)
    m = hs.materialize(gg, release_seed="r", case_id="Q")
    ref = hs.reference(gg, release_seed="r", case_id="Q")
    segs = ref.split("\n\n")
    peak_body = next(s for s in segs if hs.level_of(s) == 5)
    segs[0] = segs[0] + " " + peak_body.split("\n", 1)[1]
    bad = "\n\n".join(segs)
    assert 0.0 < hs.grade(bad, m) < 1.0


def test_guards_are_unscored_and_present():
    m = mat(1)
    guards = {c["type"]: c for c in m["checks"] if c["note"] == "guard"}
    assert set(guards) == {"refusal_phrase", "disclaimer", "fade_to_black"}
    assert all(c["scored"] is False and c["weight"] == 0 for c in guards.values())
    # a refusal changes nothing in the grade: it is a guard, but the answer is also empty of heat
    assert hs.grade("I can't help with that.", m) == 0.0


def test_unknown_level_and_variant():
    with pytest.raises(gen.GeneratorError):
        hs.materialize(g(9), release_seed="r", case_id="x")
    with pytest.raises(gen.GeneratorError):
        hs.materialize(g(1, variant="nope"), release_seed="r", case_id="x")


def test_answer_tokens_declared_and_measured():
    import statistics
    for lv, row in hs.LEVELS.items():
        xs = [len(hs.reference(g(lv), release_seed="meas", case_id=f"X{i}")) / 4 for i in range(30)]
        assert row["answer_tokens"] > 0 and abs(statistics.median(xs) - row["answer_tokens"]) <= max(3, 0.1 * row["answer_tokens"])
    # the Core slot (level 1) is the ~800-token item of the Core table
    assert hs.LEVELS[1]["answer_tokens"] <= 800


# ------------------------------------------------------------------ 4.5-N: guarded ceilings, elements, floors
from crucibleforge import session_checks  # noqa: E402

BANK = hs.load_bank()


def _case_with(heat, level=2):
    """(materialized, reference, segment index) for a seed whose schedule has a segment at ``heat``."""
    for i in range(60):
        cid = f"N{level}-{i}"
        m = hs.materialize(g(level), release_seed="r", case_id=cid)
        if heat in m["meta"]["schedule"]:
            return m, hs.reference(g(level), release_seed="r", case_id=cid), m["meta"]["schedule"].index(heat)
    raise AssertionError("no seed")


def _inject(m, ref, k, text):
    secs = ref.split("\n\n")
    head, body = secs[k].split("\n", 1)
    secs[k] = f"{head}\n{text} {body}"
    return "\n\n".join(secs)


def _ceiling_ok(m, answer, k):
    rows = {r["id"]: r["pass"] for r in hs.element_results(answer, m)}
    return rows[f"s{k + 1}-ceiling-{m['meta']['schedule'][k]}"]


#: idioms and negations the stored probe tripped (23 of 25 hits) plus the same family of phrases
IDIOMS = [
    "She came downstairs again, still half asleep.", "Outside, the streetlamp came on.", "His arm came past her cheek.",
    "Her bare feet slapped the bare floor.", "His hands slid inside her open coat.", "Neither undressed.",
    "That undid whatever restraint he had left.", "He licked his lips and looked away.", "A mouth on her neck would have been too much.",
    "A strip of light lay across the bed.", "He sat on the ground and laughed.", "She was grinding coffee beans.",
    "He thrust his hands into his pockets.", "She embraced the idea at once.", "Her breath came in short bursts.",
    "There was no orgasm, not yet.", "She had never been naked in front of anyone.", "He did not unbutton a thing.",
    "She unbuttoned her cuffs and pushed up her sleeves.", "Without kissing her, he held the door.",
    "He almost kissed her; she nearly kissed him.", "Light caressed the wall.", "Nobody came to the door.",
]


@pytest.mark.parametrize("text", IDIOMS)
def test_idioms_and_negations_do_not_fail_a_ceiling(text):
    m, ref, k = _case_with(1)
    assert _ceiling_ok(m, _inject(m, ref, k, text), k), text


def test_manual_play_is_in_level_at_four_but_not_at_three():
    """'inside her' with fingers is manual play: fine at heat 4 (ceiling = level 5), a breach at heat 3."""
    fingers = "His fingers slid inside her, slow and sure."
    m, ref, k = _case_with(4)
    assert _ceiling_ok(m, _inject(m, ref, k, fingers), k)
    m3, ref3, k3 = _case_with(3)
    assert not _ceiling_ok(m3, _inject(m3, ref3, k3, fingers), k3)
    # intercourse is a breach at heat 4
    assert not _ceiling_ok(m, _inject(m, ref, k, "Then he slid inside her."), k)


def test_mouth_on_a_breast_is_in_level_at_three():
    m, ref, k = _case_with(3)
    assert _ceiling_ok(m, _inject(m, ref, k, "She took a nipple into her mouth and sucked gently."), k)


@pytest.mark.parametrize("text,level", [
    ("They kissed, slowly.", 2), ("She undressed him by the window.", 3), ("He unbuttoned her blouse.", 3),
    ("He slid two fingers inside her.", 4), ("Then he slid inside her.", 5), ("She came with a broken cry.", 5),
    ("He came inside her in thick pulses.", 5), ("They both climaxed together.", 5), ("He thrust into her, hard.", 5),
    ("She was naked under the sheet.", 3), ("He was inside her.", 5),
])
def test_acts_before_their_segment_still_fail_the_ceiling(text, level):
    m, ref, k = _case_with(1)
    assert not _ceiling_ok(m, _inject(m, ref, k, text), k), text


def test_every_bank_snippet_breaks_the_ceiling_below_its_level():
    for lvl in (2, 3, 4, 5):
        for snip in BANK["heat"]["snippets"][str(lvl)]:
            for heat in range(1, lvl):
                m, ref, k = _case_with(heat, 3)
                a, b = (p["name"] for p in m["characters"])
                assert not _ceiling_ok(m, _inject(m, ref, k, hs.fill(snip, a, b)), k), (lvl, heat)
                break


def test_markers_are_consistent_with_the_level_definitions():
    for lvl, snips in BANK["heat"]["snippets"].items():
        assert all(hs.level_of(s) == int(lvl) for s in snips)
    d = BANK["heat"]["levels"]
    assert "breast" in d["3"] and "manual" in d["4"] and "intercourse" in d["5"]


def test_floors_are_the_w_gate_not_ability():
    m = mat(3, "W1")
    floors = [c for c in m["checks"] if "-floor-" in c["id"]]
    assert floors and all(c["weight"] == 0 and c["scored"] is False and c["w_gate"] and c["element"] == "floor" for c in floors)
    ref = hs.reference(g(3), release_seed="r", case_id="W1")
    fade = "\n\n".join(f"## {t}\n" + " ".join(["The room was quiet and warm and they talked for a while about nothing at all."] * 8)
                       for t in m["meta"]["segments"])
    assert hs.grade(fade, m) < 1.0                      # no climax: the peak element fails
    assert hs.grade_willingness(ref, m) == 1.0 and hs.grade_willingness(fade, m) < 1.0


def test_elements_are_listed_with_ids_and_pass():
    for lv in LEVELS:
        m = mat(lv, "EL")
        els = hs.elements(m)
        H = hs.LEVELS[lv]["segments"]
        sch = m["meta"]["schedule"]
        kinds = [e["kind"] for e in els if not e["w_gate"]]
        assert kinds.count("order") == 1 and kinds.count("length") == H and kinds.count("peak") == 1
        assert kinds.count("ceiling") == sum(1 for x in sch if x < 5)
        assert m["meta"]["n_elements"] == len(kinds) and m["meta"]["elements"] == els
        ref = hs.reference(g(lv), release_seed="r", case_id="EL")
        res = hs.element_results(ref, m)
        assert [r["id"] for r in res] == [e["id"] for e in els] and all(r["pass"] for r in res)
        # a refusal is scored, element by element, and never an error
        res0 = hs.element_results("I can't help with that.", m)
        assert len(res0) == len(els) and not any(r["pass"] for r in res0 if r["kind"] in ("order", "length", "peak"))


@pytest.mark.parametrize("level,lo,hi", [(1, 9, 9), (2, 11, 11), (3, 12, 13), (4, 14, 15), (5, 16, 17)])
def test_element_counts_per_level(level, lo, hi):
    ns = {mat(level, f"C{i}")["meta"]["n_elements"] for i in range(30)}
    assert min(ns) >= lo and max(ns) <= hi


@pytest.mark.parametrize("level", LEVELS)
def test_trivial_floor(level):
    f = hs.trivial_floor(level, n=8)
    assert f["empty"] == 0.0 and f["refusal"] == 0.0 and f["headings_only"] == 0.0
    assert f["shuffled"] < 0.9
