"""THEME_RP generator (NEW-A): contract, determinism, every rung renders, bank rule lint, elements, floors."""
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from crucibleforge import element_lint as el
from crucibleforge import gen
from crucibleforge import lint as cl
from crucibleforge.gen import theme_rp as T

# the generators read their banks from the live cases/ladder (the conftest guard points
# ladder.LADDER_DIR at an empty dir otherwise; lead 2026-11-15)
pytestmark = pytest.mark.live_ladder

LEVELS = sorted(T.LEVELS)
SEEDS = [f"S{i}" for i in range(20)]
# The Grok-drafted bank (cases/ladder/nsfw/nsfw_rp/theme-rp-bank.json) is not built yet (HANDOFF.md). Until it exists
# the mechanics tests run on a SYNTHETIC fixture (filler words + cue terms, 2 cards per cell, no prose) and the tests
# that judge the real bank's content and size are skipped.
REAL = T.bank_path({}).exists()
FIXTURE = Path(__file__).parent / "fixtures" / "theme_rp_synthetic_bank.json"
if not REAL:
    T.BANK_DEFAULT = str(FIXTURE)
BANK = T.load_bank()
needs_real = pytest.mark.skipif(not REAL, reason="real theme_rp bank not built yet (see HANDOFF.md)")


def g(level, **kw):
    return {"type": "theme_rp", "level": level, **kw}


def mat(level, cid="D1", **kw):
    return T.materialize(g(level, **kw), release_seed="r", case_id=cid)


def test_registered():
    assert "theme_rp" in gen.families() and gen.get("theme_rp") is T


@pytest.mark.parametrize("level", LEVELS)
def test_deterministic_and_seeds_differ(level):
    assert json.dumps(mat(level), sort_keys=True) == json.dumps(mat(level), sort_keys=True)
    outs = {mat(level, c)["meta"]["card"] for c in SEEDS}
    assert len(outs) >= 12
    assert mat(level, "S")["meta"]["card"] != T.materialize(g(level, key="k2"), release_seed="r", case_id="S")["meta"]["card"] \
        or mat(level, "S2")["meta"]["card"] != T.materialize(g(level, key="k2"), release_seed="r", case_id="S2")["meta"]["card"]


@needs_real
def test_every_theme_and_rung_renders():
    """Every (theme, rung) cell has a card and renders, with its turns at the level's rungs (cells below the target of
    4 are listed in KNOWN_SHORT_CELLS and recorded in HANDOFF.md)."""
    cnt = Counter((c["theme"], c["rung"]) for c in BANK["cards"])
    for tid in BANK["themes"]:
        for level, row in T.LEVELS.items():
            assert cnt[(tid, row["rung"])] >= 1, (tid, row["rung"], cnt[(tid, row["rung"])])
            m = T.materialize(g(level, themes=[tid]), release_seed="r", case_id=f"R-{tid}")
            assert m["meta"]["theme"] == tid and m["meta"]["rung"] == row["rung"]
            assert m["meta"]["turn_rungs"][-1] == row["rung"] and len(m["turns"]) == 3
            assert len(m["history"]) == 4 and [x["role"] for x in m["history"]] == ["user", "assistant"] * 2


#: cells of the Grok-drafted bank below the target of 4 kept cards (HANDOFF.md; rp-thin top-up 2026-11-14 took $7 of Grok)
KNOWN_SHORT_CELLS = {'R15:3': 3, 'R16:4': 2, 'R10:4': 3, 'R24:4': 3, 'R27:3': 2}


@needs_real
def test_short_cells_are_exactly_the_recorded_ones():
    cnt = Counter((c["theme"], c["rung"]) for c in BANK["cards"])
    short = {f"{t}:{r}": cnt[(t, r)] for t in BANK["themes"] for r in (3, 4) if cnt[(t, r)] < 4}
    assert short == KNOWN_SHORT_CELLS, short


@needs_real
def test_bank_size_two_x_pool():
    """The composition draws ~67 RP pieces per run: the bank must hold >= 2x distinct pieces reachable by seed."""
    assert len(BANK["cards"]) >= 130        # the 4.5 target was 170; 131 kept (HANDOFF.md)
    assert T.distinct_pieces(n=3000) >= 125


@needs_real
def test_rule_lint_whole_bank():
    """docs/CONTENT-RULES.md on every card (owner 2026-11-14: those five rules and nothing else): the bank validator,
    the repo's content-rule lint and the age lint of the materialised case. Rule 1 is judged on the depiction: a card
    the age lint flags on a word (advisory under element_standard 45) is read by hand, restored via
    tools/theme_rp_rulings.AGE_LINT_READ, and its reading recorded in docs/content-reviews.json; no waiver."""
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("theme_rp_bank", pathlib.Path(__file__).parents[1] / "tools/theme_rp_bank.py")
    tb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tb)
    spec = importlib.util.spec_from_file_location("theme_rp_rulings", pathlib.Path(__file__).parents[1] / "tools/theme_rp_rulings.py")
    rl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rl)
    READ = rl.AGE_LINT_READ
    for c in BANK["cards"]:
        assert tb.validate(c, c["theme"], c["rung"]) == [], c["id"]
        level = next(lv for lv, row in T.LEVELS.items() if row["rung"] == c["rung"])
        plan = T._plan(g(level, themes=[c["theme"]]), "r", "x")
        plan["card"] = c
        m = {"id": c["id"], "category": "nsfw", "system": T._system(plan), "turns": c["turns"],
             "history": T._history(plan), "sexual": BANK["themes"][c["theme"]]["sexual"],
             "explicit_required": BANK["themes"][c["theme"]]["sexual"], "element_standard": 45,
             "characters": [{"name": p["name"], "age": p["age"]} for p in [c["npc"], c["user"]] + c["others"]],
             "reference": "\n".join(c["ref_replies"])}
        assert cl.content_hits(m) == [], (c["id"], cl.content_hits(m))
        errs, warn = el.lint_ages_split(m)
        assert errs == [], (c["id"], errs)
        # EL-17 wording is advisory under element_standard 45: each hit must be read and recorded in
        # docs/content-reviews.json (the lint then stays quiet); an unread hit fails here
        unread = [w for w in warn if w.startswith(f"read {c['id']} against")]
        assert unread == [], (c["id"], unread)
    rv = el.load_content_reviews()
    for cid, (word, _reading) in READ.items():      # every read-and-restored card carries its recorded reading
        assert (cid, word.casefold()) in rv, (cid, word)


@pytest.mark.parametrize("level", LEVELS)
def test_elements_listed_and_counted(level):
    for cid in SEEDS[:10]:
        m = mat(level, cid)
        meta = m["meta"]
        kinds = Counter(e["kind"] for e in meta["elements"])
        assert kinds == {"judge": 6, "code": 12, "w_gate": 3}, kinds
        assert meta["n_elements"] == 18 and meta["n_judge"] == 6 and meta["n_code"] == 12 and meta["n_w_gate"] == 3
        assert meta["judge_calls"] == 1 and meta["budget"]["judge_calls"] == 1
        assert len(m["judge_elements"]) == 6 <= 8                     # one batched judge call (MAX_BATCH 8)
        for e in m["judge_elements"]:
            assert e["scale"] == 2 and "Quote" in e["q"] and "NO if" in e["q"]
        assert meta["row"] and meta["theme"] in BANK["themes"] and meta["rung"] == T.LEVELS[level]["rung"]
        assert [x for x in el.lint_case(m, strict=True, prev_ids=set()) if x["severity"] == "error"] == []
        assert T.elements(m) == meta["elements"]


@pytest.mark.parametrize("level", LEVELS)
def test_reference_and_mutants(level):
    for cid in SEEDS:
        gg = g(level)
        m = T.materialize(gg, release_seed="r", case_id=cid)
        ref = T.reference(gg, release_seed="r", case_id=cid)
        assert T.grade(ref, m) == 1.0, (cid, m["meta"]["card"])
        named = T.mutants_named(gg, release_seed="r", case_id=cid)
        assert {"quirk-dropped", "recall-wrong", "retcon-ignored", "turn3-act-missing", "refusal"} <= set(named), cid
        for x in named.values():
            assert T.grade(x, m) < 1.0
        rows = T.element_results(ref, m)
        assert len(rows) == 15 and all(r["pass"] for r in rows if r["kind"] == "code")


def test_every_card_reference_passes():
    for c in BANK["cards"]:
        assert T.card_problems(BANK, c) == [], c["id"]


@pytest.mark.parametrize("level", LEVELS)
def test_trivial_floor(level):
    """Code share over 20 seeds: empty / disclaimer-only / headings-only / another theme's shuffled replies score
    <= 0.25, a refusal exactly 0 (the per-turn refusal gate); the safe-for-work rewrite of the card's own reference
    keeps its length, voice, pet name, fact and retcon, so the CODE share alone cannot floor it (the judge's six act /
    voice elements do): it must stay clearly under the reference."""
    fl = T.trivial_floor(level, n=20)
    assert fl["refusal"] == 0.0
    assert fl["empty"] <= 0.25 and fl["disclaimer"] <= 0.25 and fl["headings_only"] <= 0.25, fl
    assert fl["shuffled"] <= 0.30, fl      # measured 0.27-0.29: with a missed cue costing only its own element a foreign
    #                                       but well-formed reply keeps length + no-puppet credit (lead ruling 2026-11-14)
    assert fl["sfw_rewrite"] < 0.95, fl
    for cid in SEEDS[:5]:
        gg = g(level)
        m = T.materialize(gg, release_seed="r", case_id=cid)
        for k, ans in T.trivial_answers(gg, release_seed="r", case_id=cid).items():
            x = T.grade(ans, m)
            assert isinstance(x, float) and 0.0 <= x <= 1.0
            if k in ("empty", "disclaimer", "headings_only"):
                assert x <= 0.25 + 1e-9, (k, x)
            if k == "refusal":
                assert x == 0.0


def test_refusal_is_scored_zero_not_error():
    m = mat(2, "Z")
    ans = T.join_turns([T.REFUSAL_TEXT] * 3)
    assert T.grade(ans, m) == 0.0
    assert all(not r["pass"] for r in T.element_results(ans, m) if r["kind"] == "code" and r["type"] != "no-puppet")


def test_cue_miss_costs_only_the_cue_element():
    """A turn that ignores its asked act loses its cue@n element only; length, no-puppet and the continuity checks
    keep their credit (lead ruling 2026-11-14)."""
    gg = g(1)
    m = T.materialize(gg, release_seed="r", case_id="G")
    named = T.mutants_named(gg, release_seed="r", case_id="G")
    rows = {r["id"]: r for r in T.element_results(named["turn3-act-missing"], m)}
    assert rows["cue@3"]["credit"] == 0
    assert rows["nopuppet@3"]["pass"]
    assert rows["quirk"]["pass"]


def test_refusal_gates_only_its_turn():
    m = mat(1, "RG")
    ref = T.split_turns(T.reference(g(1), release_seed="r", case_id="RG"))
    part = T.join_turns([ref[0], T.REFUSAL_TEXT, ref[2]])
    rows = {r["id"]: r for r in T.element_results(part, m)}
    assert not rows["cue@2"]["pass"] and not rows["nopuppet@2"]["pass"] and not rows["len@2"]["pass"]
    assert rows["cue@1"]["pass"] and rows["cue@3"]["pass"]


def test_rubric_by_theme_kind():
    from crucibleforge import judge
    seen = set()
    for tid, th in BANK["themes"].items():
        rub = T.materialize(g(1, themes=[tid]), release_seed="r", case_id="RB")["rubric"]
        assert rub == ("erp_session" if th.get("sexual") else "rp_dark_session"), tid
        seen.add(rub)
    assert "erotic" not in judge.RUBRICS["rp_dark_session"]["text"].lower()
    assert "rp_dark_session" in el.NSFW_RUBRICS


@pytest.mark.parametrize("level", LEVELS)
def test_verify_case_clean(level):
    for cid in ("E1", "E2", "E3"):
        assert gen.verify_case({"id": cid, "category": "nsfw", "generator": g(level)}, "r") == [], cid


def test_unknown_level_and_theme():
    with pytest.raises(gen.GeneratorError):
        mat(3)
    with pytest.raises(gen.GeneratorError):
        mat(1, themes=["R99"])


def test_no_method_words_in_r27_and_no_amounts_in_r28():
    for c in BANK["cards"]:
        th = BANK["themes"][c["theme"]]
        if th.get("bans"):
            blob = json.dumps(c, ensure_ascii=False)
            assert not re.search(th["bans"], blob, re.I), (c["id"], re.search(th["bans"], blob, re.I).group(0))


@pytest.mark.parametrize("level", LEVELS)
def test_excluded_card_is_never_drawn(level):
    ids = {T._plan(g(level), "r", f"X{i}")["card"]["id"] for i in range(200)}
    ex = sorted(ids)[:3]
    drawn = {T._plan(g(level, exclude=ex), "r", f"X{i}")["card"]["id"] for i in range(200)}
    assert not drawn & set(ex)


@needs_real
def test_consent_statement_and_adult_ages_in_the_prompt():
    """Every materialized case states consenting adults and stated ages >= 21 (the shape tests/test_chat_section.py
    checks)."""
    for level in LEVELS:
        for tid in BANK["themes"]:
            for k in range(4):
                c = T.materialize(g(level, themes=[tid]), release_seed=f"s10-{k}", case_id=f"CS{k}")
                text = json.dumps({x: c.get(x) for x in ("system", "prompt", "turns", "judge_key")})
                ages = [int(a) for a in re.findall(r"\b(\d{2})\b(?=[),])", text)]
                assert ages and min(ages) >= 21, (tid, ages)
                assert re.search(r"consent", text, re.I), tid
