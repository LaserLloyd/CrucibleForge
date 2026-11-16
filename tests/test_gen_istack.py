"""ISTACK generator (G4): determinism, seeds differ, reference 1.0, mutants < 1.0, lazy 0.0,
sizes inside the docstring table for 30 seeds per level, engine shape."""
import json

import pytest

from crucibleforge import session_checks
from crucibleforge.gen import istack

SEEDS = [f"X{i}" for i in range(30)]
# level -> (prompt tokens, reference tokens, checks) bounds, per variant; mirrors the docstring table
SIZES = {
    "write": {1: ((194, 228), (54, 138), (5, 5)), 2: ((258, 299), (64, 187), (8, 8)), 3: ((409, 518), (146, 204), (12, 12)), 4: ((447, 555), (132, 214), (16, 16)), 5: ((536, 602), (164, 236), (20, 20)), 6: ((537, 606), (158, 240), (21, 21)), 7: ((482, 596), (96, 164), (18, 18)), 8: ((526, 630), (142, 369), (20, 20))},
    "conv": {1: ((332, 396), (167, 450), (9, 15)), 2: ((416, 474), (188, 423), (15, 22)), 3: ((552, 674), (271, 577), (20, 29)), 4: ((600, 719), (292, 590), (25, 34)), 5: ((671, 770), (360, 647), (32, 42)), 6: ((682, 778), (332, 654), (28, 42)), 7: ((764, 878), (246, 422), (33, 42)), 8: ((802, 925), (385, 821), (36, 43))},
}
CASES = [(v, lv) for v in ("write", "conv") for lv in range(1, 9)]


def gen_of(variant, level, **kw):
    return {"type": "istack", "level": level, "variant": variant, **kw}


def mat(variant, level, cid):
    return istack.materialize(gen_of(variant, level), release_seed="r", case_id=cid)


@pytest.mark.parametrize("variant,level", CASES)
def test_deterministic(variant, level):
    a, b = mat(variant, level, "D1"), mat(variant, level, "D1")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    g = gen_of(variant, level)
    assert istack.reference(g, release_seed="r", case_id="D1") == istack.reference(g, release_seed="r", case_id="D1")
    assert istack.mutants(g, release_seed="r", case_id="D1") == istack.mutants(g, release_seed="r", case_id="D1")


@pytest.mark.parametrize("variant", ["write", "conv"])
def test_seeds_differ(variant):
    outs = {json.dumps(mat(variant, 3, c)["turns"]) for c in SEEDS[:10]}
    assert len(outs) == 10
    base = mat(variant, 3, "S")
    assert base["turns"] != istack.materialize(gen_of(variant, 3, key="other"), release_seed="r", case_id="S")["turns"]
    assert base["turns"] != istack.materialize(gen_of(variant, 3), release_seed="r2", case_id="S")["turns"]


@pytest.mark.parametrize("variant,level", CASES)
def test_reference_mutants_lazy_and_sizes(variant, level):
    (plo, phi), (rlo, rhi), (clo, chi) = SIZES[variant][level]
    ps, rs, cs = [], [], []
    for cid in SEEDS:
        g = gen_of(variant, level)
        m = istack.materialize(g, release_seed="r", case_id=cid)
        ref = istack.reference(g, release_seed="r", case_id=cid)
        assert istack.grade(ref, m) == 1.0, cid
        ms = istack.mutants(g, release_seed="r", case_id=cid)
        assert len(ms) >= 3, cid
        for x in ms:
            assert istack.grade(x, m) < 1.0, (cid, x[:80])
        assert istack.grade("", m) == 0.0
        assert istack.grade("Here you go.", m) == 0.0
        assert istack.grade(istack.TURN_SEP.join(["Here you go."] * 3), m) == 0.0
        ps.append(m["meta"]["prompt_tokens_est"])
        rs.append(m["meta"]["reference_tokens_est"])
        cs.append(m["meta"]["n_checks"])
    assert plo <= min(ps) and max(ps) <= phi, (min(ps), max(ps))
    assert rlo <= min(rs) and max(rs) <= rhi, (min(rs), max(rs))
    assert clo <= min(cs) and max(cs) <= chi, (min(cs), max(cs))


@pytest.mark.parametrize("variant", ["write", "conv"])
def test_engine_shape_and_agreement(variant):
    m = mat(variant, 5, "E1")
    assert m["category"] == "rpdeep" and m["programmatic"] is True
    assert len(m["turns"]) == (1 if variant == "write" else 3)
    ids = [c["id"] for c in m["checks"]]
    assert len(ids) == len(set(ids))
    assert all(c["fact"] == c["id"] and (c["weight"] > 0 or c.get("gate")) and c["type"] in session_checks.CHECKS for c in m["checks"])
    assert {c["weight"] for c in m["checks"]} >= {1, 2}           # facts / order / topic weigh x2; passive rules < 1
    ref = istack.reference(gen_of(variant, 5), release_seed="r", case_id="E1")
    run = session_checks.run_checks(m, istack.split_turns(ref))
    assert all(r["pass"] for r in run["results"]) and run["rate"] == 1.0
    assert all(c["turns"][0] <= len(m["turns"]) for c in m["checks"])


def test_n_constraints_follow_levels():
    for lv, n in [(1, 4), (2, 7), (3, 10), (4, 13), (5, 16)]:
        assert mat("write", lv, "N1")["meta"]["n_constraints"] == n


def test_forced_interaction_at_high_levels():
    for cid in SEEDS[:10]:
        kinds = set(mat("write", 4, cid)["meta"]["kinds"])
        assert {"window", "acro", "facts", "ents"} <= kinds


def test_facts_are_verbatim_in_reference_and_sfw():
    for cid in SEEDS[:10]:
        m = mat("write", 5, cid)
        ref = istack.reference(gen_of("write", 5), release_seed="r", case_id=cid)
        facts = [c for c in m["checks"] if c["note"] == "fact"]
        assert len(facts) == 4
        for f in facts:
            assert f["weight"] == 2
        assert not any(ch.isdigit() for ch in ref)          # level 5 carries no-digits, facts are spelled out


def test_conv_window_change_and_cross_turn_reference():
    seen_change = False
    for cid in SEEDS:
        m = mat("conv", 4, cid)
        assert "standing rule" in m["turns"][1].lower() or "still applies" in m["turns"][1]
        seen_change |= any("CHANGE:" in t for t in m["turns"])
    assert seen_change


def test_module_main_prints_one_item(capsys):
    istack._main([])
    out = capsys.readouterr().out
    assert "USER TURN 1" in out and "grade(reference) = 1.0" in out


# ---------------------------------------------------------------- 4.5 Core levels 7-8 (A4): exact counts, rule changes
import re  # noqa: E402
import hashlib  # noqa: E402

HI = [(v, lv) for v in ("write", "conv") for lv in (7, 8)]


def _count_words(t):
    return len(re.findall(r"[A-Za-z0-9']+", t))


def _paras(text, closing=None):
    """Body paragraphs of a reply: not a heading, not the closing line."""
    return [b for b in text.split("\n\n") if b.strip() and not b.startswith("#") and b != closing]


@pytest.mark.parametrize("variant,level", HI)
def test_levels_use_the_new_kinds_and_budget(variant, level):
    want = {7: {"write": ["pwords", "lcount"], "conv": ["pwords", "lcount"]},
            8: {"write": ["pwords", "twords", "swords", "lcount"], "conv": ["pwords", "swords", "lcount"]}}[level][variant]
    for cid in SEEDS[:10]:
        m = mat(variant, level, cid)
        kinds = m["meta"]["kinds"]
        assert [k for k in kinds if k in ("pwords", "twords", "swords", "lcount")] == want, (cid, kinds)
        assert m["max_tokens"] == 6144
        assert len(kinds) == 12 + len(want)
        if level == 7:
            assert "window" in kinds                                                 # forced at L7
        if (level, variant) == (8, "write"):
            assert "window" not in kinds                                             # twords replaces window
        if level == 8:
            assert "sents" not in kinds                                              # swords replaces sents
        # the total goes through the ENGINE word_range grader, every other count is a weight-1 check
        tw = [c for c in m["checks"] if c["note"] == "twords"]
        assert all(c["type"] == "word_range" and c["min"] == c["max"] and c["tolerance"] == 0 for c in tw)
        for c in m["checks"]:
            if c["note"] in ("pwords", "twords", "swords", "lcount"):
                assert c["weight"] == 1 and not c.get("positive")      # light: a miss costs points, never a turn gate
    assert mat("write", 3, "N1")["max_tokens"] == 3000


def _turn_facts(m, ref, turns):
    """What the reference actually does, measured by an independent counter (not the generator's)."""
    out = []
    for t, text in enumerate(istack.split_turns(ref), 1):
        paras = _paras(text, turns[t - 1].get("closing"))
        out.append({"t": t, "paras": [_count_words(p) for p in paras], "total": _count_words(text),
                    "sent_words": sorted({_count_words(x) for p in paras for x in re.findall(r"[^.!?]+[.!?]", p)}),
                    "text": text})
    return out


@pytest.mark.parametrize("variant,level", HI)
def test_reference_meets_each_stated_count(variant, level):
    for cid in SEEDS[:12]:
        g = gen_of(variant, level)
        m = istack.materialize(g, release_seed="r", case_id=cid)
        plan = istack._plan(g, "r", cid)
        for f, a in zip(_turn_facts(m, istack.reference(g, release_seed="r", case_id=cid), plan["turns"]), plan["turns"]):
            if "pwords" in a:
                assert f["paras"] == a["pwords"], (cid, f["t"])
            if "twords" in a:
                assert f["total"] == a["twords"], (cid, f["t"])
            if "swords" in a:
                assert f["sent_words"] == [a["swords"]], (cid, f["t"])
            if "lcount" in a:
                ch, n, _ = a["lcount"]
                assert f["text"].lower().count(ch) == n, (cid, f["t"])
            if "pwords" in a and "swords" in a:
                assert all(n % a["swords"] == 0 for n in a["pwords"])


@pytest.mark.parametrize("level", [7, 8])
def test_conv_flips_and_scope(level):
    for cid in SEEDS[:12]:
        g = gen_of("conv", level)
        m = istack.materialize(g, release_seed="r", case_id=cid)
        plan = istack._plan(g, "r", cid)
        t1, t2, t3 = plan["turns"]
        w1 = plan["it"]["forbw"][0]
        assert w1 in t1["forbw"] and "kwf" not in t1                         # forbidden in piece 1 ...
        assert t2["kwf"][0] == w1 and t3["kwf"][0] == w1 and w1 not in t2["forbw"]   # ... wanted n times from piece 2
        assert "CHANGE: the word" in m["turns"][1]
        assert "semis_scope" in t3 and "semis_scope" not in t2 and "odd-numbered" in m["turns"][2]
        assert ("CHANGE: the acrostic word" in m["turns"][2]) == (level == 8)
        assert (t3["acro"] != t1["acro"]) == (level == 8) and len(t3["acro"]) == len(t1["acro"])
        ids = {c["id"] for c in m["checks"]}
        assert "t2-kwf" in ids and "t3-kwf" in ids and "t1-kwf" not in ids
        w = {c["id"]: c["weight"] for c in m["checks"]}
        assert w["t2-kwf"] == 2


@pytest.mark.parametrize("variant,level", HI)
def test_named_mutants_cost_only_their_own_check(variant, level):
    for cid in SEEDS[:12]:
        g = gen_of(variant, level)
        m = istack.materialize(g, release_seed="r", case_id=cid)
        named = istack.mutants_named(g, release_seed="r", case_id=cid)
        assert "count-off-by-one" in named
        assert ("flip-ignored" in named) == (variant == "conv") and ("scope-ignored" in named) == (variant == "conv")
        allowed = {"count-off-by-one": {"pwords", "twords", "swords", "window"}, "flip-ignored": {"kwf"},
                   "scope-ignored": {"semis"}}
        for name, ans in named.items():
            if name not in allowed:
                continue
            run = session_checks.run_checks(m, istack.split_turns(ans))
            failed = {r["id"] for r in run["results"] if not r["pass"]}
            assert failed, (cid, name)
            assert {re.sub(r"^t\d+-", "", f) for f in failed} <= allowed[name], (cid, name, failed)
            sc = istack.grade(ans, m)
            assert 0.6 <= sc < 1.0, (cid, name, sc)               # costs its own check, never a turn gate


def test_ents_accepts_a_capital_at_the_start_of_a_sentence():
    """The `ents` case fix: "The heron" at a sentence start scores like "the heron" (the old check was case-sensitive)."""
    seen = 0
    for lv, variant in ((4, "write"), (8, "write")):
        for cid in SEEDS[:10]:
            g = gen_of(variant, lv)
            m = istack.materialize(g, release_seed="r", case_id=cid)
            plan = istack._plan(g, "r", cid)
            ref = istack.reference(g, release_seed="r", case_id=cid)
            for e in plan["it"]["ents"]:
                if e[0].islower():
                    cap = ref.replace(e, e[0].upper() + e[1:], 1)
                    assert cap != ref and istack.grade(cap, m) == 1.0, (cid, e)
                    seen += 1
    assert seen > 10
    assert "a capital at the start of a sentence is fine" in mat("write", 4, "X0")["turns"][0]


def test_levels_1_to_6_references_mutants_and_checks_are_unchanged():
    """Frozen digest (reference answers, mutant answers, check ids of 3 items per level 1-6), computed on the pre-4.5
    generator. The `ents` case fix and its prompt note are the only deliberate difference, and they change neither.
    Re-pinned by the H1 fairness pass (2026-11-14): an item whose entity head word occurs in its topic (a ferry story
    with 'the ferry') is re-drawn, and the ents / facts graders accept more (head word, sentence-start capital)."""
    h = hashlib.sha256()
    for v in ("write", "conv"):
        for lv in range(1, 7):
            for cid in ("X0", "X1", "X2"):
                g = gen_of(v, lv)
                m = istack.materialize(g, release_seed="r", case_id=cid)
                h.update(json.dumps([c["id"] for c in m["checks"]]).encode())
                h.update(istack.reference(g, release_seed="r", case_id=cid).encode())
                h.update(json.dumps(istack.mutants(g, release_seed="r", case_id=cid)).encode())
    assert h.hexdigest() == "eb06ea775d8a37594371de06628efec7b2cc3706178865538f4e93bb719efe1e"


def test_shared_module_pin():
    """chains.revision() digests only istack.py: the shared grader module is pinned so editing it stales stored rows"""
    import hashlib
    from pathlib import Path
    d = Path(istack.__file__).parent
    assert hashlib.sha256((d / "_stack_common.py").read_bytes()).hexdigest()[:16] == istack._STACK_SHA


def test_ents_accept_any_article_or_describing_word_and_heads_never_collide():
    """H1 fairness pass: 'a heron' / 'the old heron' / 'Pell' are mentions (the prompt says so); no head word occurs in
    the item's other texts, so the order check reads only the model's mentions."""
    import re
    seen = 0
    for lv, variant in ((4, "write"), (7, "write"), (8, "write"), (4, "conv")):
        for cid in SEEDS[:12]:
            g = gen_of(variant, lv)
            m = istack.materialize(g, release_seed="r", case_id=cid)
            plan = istack._plan(g, "r", cid)
            ref = istack.reference(g, release_seed="r", case_id=cid)
            assert "first appear in that order" in "".join(m["turns"])
            for e in plan["it"]["ents"]:
                h = istack._head(e)
                if e != h:
                    alt = ref.replace(e, " ".join(["one"] + e.split()[1:]))       # same word count, other article
                    assert istack.grade(alt, m) == 1.0, (cid, e)
                    seen += 1
                others = " ".join(t[1] + " " + t[2] for t in plan["it"]["topics"]).lower()
                assert not re.search(rf"\b{h.lower()}s?\b", others), (cid, e)
            # reversing two entities' first mentions still fails
    assert seen > 30


def test_facts_accept_a_capital_at_the_start_of_a_sentence():
    seen = 0
    for cid in SEEDS[:15]:
        g = gen_of("write", 7)
        m = istack.materialize(g, release_seed="r", case_id=cid)
        plan = istack._plan(g, "r", cid)
        ref = istack.reference(g, release_seed="r", case_id=cid)
        for _, t in plan["it"].get("facts", []):
            if t[0].islower():
                cap = ref.replace(t, t[0].upper() + t[1:], 1)
                assert istack.grade(cap, m) == 1.0, (cid, t)
                para = ref.replace(t, t.replace(" ", "  ", 1).replace("the ", "a ", 1), 1)
                assert istack.grade(para, m) < 1.0
                seen += 1
    assert seen > 10
