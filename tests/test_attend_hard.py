"""attend L7-L10 (EXP-20261114-05): the directive layer. L1-L6 must not change; the new levels stay
per-turn (every check reads one turn), deterministic, reference-clean, and not passable by lazy or naive replies."""
import hashlib
import json

import pytest

from crucibleforge import chains
from crucibleforge.gen import attend, attend_rules as ar
from crucibleforge import session_checks
from crucibleforge.gen._stack_common import TURN_SEP, grade_weighted, rng, split_turns

RS = "4.5.0"
OLD = {  # sha256[:16] of json(materialize, reference) taken on core45-integration BEFORE L7-L10 existed
    "1:EN-demo:None": "d2c3a5e1623a07a4", "1:PRB-attend-p1:p1": "4e3ff82a15753057", "1:PRB-x-7:7": "3271ce986895b6b2",
    "2:EN-demo:None": "d1edc3784fb6dd7e", "2:PRB-attend-p1:p1": "ae1dcf1a0702081b", "2:PRB-x-7:7": "67ed08eec59fb2d8",
    "3:EN-demo:None": "2ad21393ab3616f3", "3:PRB-attend-p1:p1": "d59d98a6e7bf1eaa", "3:PRB-x-7:7": "dc5db2f06911c56b",
    "4:EN-demo:None": "b605aed613933406", "4:PRB-attend-p1:p1": "a22a95ddb4657454", "4:PRB-x-7:7": "ae7386832b8875e4",
    "5:EN-demo:None": "76c06bd7e30c5b0f", "5:PRB-attend-p1:p1": "2466557e181fc7aa", "5:PRB-x-7:7": "b731b7e41b42863c",
    "6:EN-demo:None": "13757868d352846e", "6:PRB-attend-p1:p1": "17669db24802c605", "6:PRB-x-7:7": "3ee4d421ed7f7ef7",
}
HARD = (7, 8, 9, 10)
DENSE = 11     # L11: shorter than L10, denser per turn (share/exact_ns/cap_ns knobs)


def _g(level, key=None):
    g = {"type": "attend", "level": level, "variant": "host"}
    if key:
        g["key"] = key
    return g


@pytest.mark.parametrize("name", sorted(OLD))
def test_l1_to_l6_instances_unchanged(name):
    lv, cid, key = name.split(":")
    g = _g(int(lv), None if key == "None" else key)
    m = attend.materialize(g, release_seed=RS, case_id=cid)
    ref = attend.reference(g, release_seed=RS, case_id=cid)
    assert hashlib.sha256(json.dumps([m, ref], sort_keys=True).encode()).hexdigest()[:16] == OLD[name]


@pytest.mark.parametrize("lv", HARD)
def test_deterministic_and_seeded(lv):
    a = attend.materialize(_g(lv, "k1"), release_seed=RS, case_id="C")
    b = attend.materialize(_g(lv, "k1"), release_seed=RS, case_id="C")
    c = attend.materialize(_g(lv, "k2"), release_seed=RS, case_id="C")
    assert a == b
    assert a["turns"] != c["turns"]


@pytest.mark.parametrize("lv", HARD)
@pytest.mark.parametrize("key", ["a", "b", "c"])
def test_reference_passes_every_check_and_every_turn(lv, key):
    g = _g(lv, key)
    m = attend.materialize(g, release_seed=RS, case_id="C")
    ref = attend.reference(g, release_seed=RS, case_id="C")
    assert attend.grade(ref, m) == 1.0
    ts = chains.turn_scores(m["checks"], ref.split(TURN_SEP))
    assert set(ts) == set(range(1, len(m["turns"]) + 1))      # every turn is its own element
    assert all(v == 1.0 for v in ts.values())


@pytest.mark.parametrize("lv", HARD)
def test_no_check_spans_turns(lv):
    m = attend.materialize(_g(lv, "a"), release_seed=RS, case_id="C")
    assert all(len(c["turns"]) == 1 for c in m["checks"])
    assert len({c["id"] for c in m["checks"]}) == len(m["checks"])
    # the weights of the per-turn element are the weights of the checks: nothing is dropped
    assert sum(chains.turn_weights(m["checks"]).values()) == pytest.approx(sum(c["weight"] for c in m["checks"]))


@pytest.mark.parametrize("lv", HARD)
def test_literal_and_lazy_replies_score_low(lv):
    g = _g(lv, "a")
    m = attend.materialize(g, release_seed=RS, case_id="C")
    T = len(m["turns"])
    for lazy in ("Sure, noted.", "OK", ""):
        ts = chains.turn_scores(m["checks"], [lazy] * T)
        assert sum(ts.values()) / T == 0.0
    # a plain well-formed scene reply that ignores every rule (the best "no attention" strategy)
    p = attend._plan(g, RS, "C")
    means = []
    for rot in range(3):          # which character happens to speak first is luck: average the three orders
        plain = [ar.compose(p, t, rng("plain", str(t)), only=[], rot=rot) for t in range(1, T + 1)]
        means.append(sum(chains.turn_scores(m["checks"], plain).values()) / T)
    assert sum(means) / 3 < 0.5, means


@pytest.mark.parametrize("lv", (8, 9, 10))
def test_stale_state_loses_points(lv):
    """Answering turn t with the obligations of turn t-3 (rules not yet revoked, conditions not yet met)."""
    g = _g(lv, "a")
    m = attend.materialize(g, release_seed=RS, case_id="C")
    p = attend._plan(g, RS, "C")
    T = len(m["turns"])
    stale = [ar.compose(p, t, rng("stale", str(t)), only=p["obl"][max(1, t - 3)]) for t in range(1, T + 1)]
    ts = chains.turn_scores(m["checks"], stale)
    assert sum(ts.values()) / T < 0.95


@pytest.mark.parametrize("lv", HARD)
def test_each_broken_obligation_fails_its_check(lv):
    g = _g(lv, "a")
    m = attend.materialize(g, release_seed=RS, case_id="C")
    mut = attend.mutants_named(g, release_seed=RS, case_id="C")
    assert mut
    from crucibleforge import session_checks
    for name, (ans, want) in mut.items():
        out = session_checks.run_checks({"checks": m["checks"]}, ans.split(TURN_SEP))
        failed = {r["id"] for r in out["results"] if not r.get("pass")}
        assert set(want) <= failed, (name, want, failed)


def test_levels_have_the_levers():
    rows = attend.LEVELS["host"]
    for lv in HARD:
        assert rows[lv]["rules"]
    for k in ("T", "gap", "conflict", "distract", "latch", "change", "cond"):
        assert [rows[lv][k] for lv in HARD] == sorted(rows[lv][k] for lv in HARD), k
    load = [sum(rows[lv].get(k, 0) for k in ("std", "cond", "latch", "conflict", "exact", "tally", "par")) for lv in HARD]
    assert load == sorted(load) and len(set(load)) == 4, load


@pytest.mark.parametrize("lv", (9, 10))
def test_ordinal_and_tally_texts_resolve(lv):
    for key in "abcdef":
        m = attend.materialize(_g(lv, key), release_seed=RS, case_id="C")
        txt = " ".join(m["turns"])
        assert "<<ORD" not in txt


def test_flat_set_with_per_turn_elements_runs_through_chains():
    """The flat-set path (`element: turn`) accepts L7-L10 and scores the reference 1.0 on every turn element."""
    from crucibleforge import gen
    from crucibleforge.gen._stack_common import split_turns
    prof = {"name": "t", "cases": {"rpdeep": []},
            "scores": {"chat": {"parts": {"main": {"max": 10, "scoring": ["rpdeep"], "cases": []}}}},
            "chains": {"chat/main": {"flat": [
                {"family": "attend", "variant": "host", "levels": {7: 1, 8: 1, 9: 1, 10: 1}, "element": "turn",
                 "ceiling_per_turn": 4000}]}}}
    chains.check_gradeable(prof)
    for c in chains.chain_cases(prof):
        inst = chains.instance(c, 1, 1)
        case = chains.materialize(inst)
        g = inst["generator"]
        ref = gen.get(g["type"]).reference(dict(g), release_seed=gen.release_seed(), case_id=case["id"])
        rows = [{"turn": t, "response": r, "finish_reason": "stop"} for t, r in enumerate(split_turns(ref), 1)]
        els = chains.turn_elements(rows, case)
        assert len(els) == len(case["turns"]) and all(e["score"] == 1.0 for e in els)


@pytest.mark.parametrize("lv", HARD)
def test_pinned_sizes_hold(lv):
    row = attend.LEVELS["host"][lv]
    for k in range(6):
        m = attend.materialize(_g(lv, f"s{k}"), release_seed=RS, case_id=f"S{k}")
        assert row["size_prompt"][0] <= m["meta"]["prompt_tokens_est"] <= row["size_prompt"][1]
        assert row["size_checks"][0] <= m["meta"]["n_checks"] <= row["size_checks"][1]


# ---------------------------------------------------------------- L11 (density, not length)
def test_l11_is_denser_not_longer():
    rows = attend.LEVELS["host"]
    assert rows[11]["T"] <= rows[10]["T"] <= 26
    load = lambda lv: sum(rows[lv].get(k, 0) for k in ("std", "cond", "latch", "conflict", "flip", "exact", "tally", "par"))
    assert load(11) > load(10)
    assert rows[11]["ordinal"] == 1.0 and rows[11]["flip"] == 2 and rows[11]["tally"] == 2


@pytest.mark.parametrize("key", ["a", "b", "c", "d", "e", "f"])
def test_l11_reference_clean_and_per_turn(key):
    g = _g(DENSE, key)
    m = attend.materialize(g, release_seed=RS, case_id="C")
    ref = attend.reference(g, release_seed=RS, case_id="C")
    assert attend.grade(ref, m) == 1.0
    ts = chains.turn_scores(m["checks"], ref.split(TURN_SEP))
    assert set(ts) == set(range(1, len(m["turns"]) + 1)) and all(v == 1.0 for v in ts.values())
    assert all(len(c["turns"]) == 1 for c in m["checks"])
    assert "<<ORD" not in " ".join(m["turns"])


def test_l11_pinned_sizes_hold_and_lazy_fails():
    row = attend.LEVELS["host"][DENSE]
    for k in range(6):
        m = attend.materialize(_g(DENSE, f"s{k}"), release_seed=RS, case_id=f"S{k}")
        assert row["size_prompt"][0] <= m["meta"]["prompt_tokens_est"] <= row["size_prompt"][1]
        assert row["size_checks"][0] <= m["meta"]["n_checks"] <= row["size_checks"][1]
    m = attend.materialize(_g(DENSE, "a"), release_seed=RS, case_id="C")
    T = len(m["turns"])
    assert sum(chains.turn_scores(m["checks"], ["Sure, noted."] * T).values()) == 0.0


def test_l11_each_broken_obligation_fails_its_check():
    from crucibleforge import session_checks
    g = _g(DENSE, "a")
    m = attend.materialize(g, release_seed=RS, case_id="C")
    mut = attend.mutants_named(g, release_seed=RS, case_id="C")
    assert mut
    for name, (ans, want) in mut.items():
        out = session_checks.run_checks({"checks": m["checks"]}, ans.split(TURN_SEP))
        assert set(want) <= {r["id"] for r in out["results"] if not r.get("pass")}, name


def test_l11_stale_state_loses_points():
    g = _g(DENSE, "a")
    m = attend.materialize(g, release_seed=RS, case_id="C")
    p = attend._plan(g, RS, "C")
    T = len(m["turns"])
    stale = [ar.compose(p, t, rng("stale", str(t)), only=p["obl"][max(1, t - 3)]) for t in range(1, T + 1)]
    assert sum(chains.turn_scores(m["checks"], stale).values()) / T < 0.95


def test_rules_pin():
    """chains.revision() digests only attend.py: it pins attend_rules.py, so editing the sibling stales stored rows"""
    from pathlib import Path
    assert hashlib.sha256(Path(ar.__file__).read_bytes()).hexdigest()[:16] == attend._RULES_SHA


def test_voice_change_names_the_habit_and_keeps_other_rules():
    """F-054: 'Scratch Odo's manner of speech' read as wiping every Odo rule; the change line now names the habit"""
    seen = 0
    for lv in HARD + (DENSE,):
        for k in range(12):
            m = attend.materialize(_g(lv, f"p{k}"), release_seed=RS, case_id=f"PRB-attend-p{k}")
            txt = json.dumps(m)
            assert "manner of speech; here is the new version" not in txt
            if "here is the new version" in txt and "stays in force" in txt:
                seen += 1
    assert seen > 0


def test_voice_revoke_and_reinstate_name_the_habit():
    """F-054 on the other forms: 'Scrap Odo's manner of speech; it no longer applies' (L11 p1, turn 5) reads as wiping
    Odo's last-word rule too. Every line that targets a voice rule names its habit; a revoke says other rules in force stay (never read as reviving a scrapped one)."""
    seen = 0
    for lv in HARD + (DENSE,):
        for k in range(16):
            m = attend.materialize(_g(lv, f"p{k}"), release_seed=RS, case_id=f"PRB-attend-p{k}")
            txt = json.dumps(m)
            assert "manner of speech" not in txt, (lv, k)
            for t in m["turns"]:
                for verb in ("Scrap the rule that", "Forget the rule that"):
                    if verb in t:
                        seen += 1
                        assert "stays in force" in t.split(verb, 1)[1], (lv, k, t)
    assert seen > 0


def test_shared_module_pins():
    from pathlib import Path
    d = Path(ar.__file__).parent
    assert hashlib.sha256((d / "ensemble.py").read_bytes()).hexdigest()[:16] == attend._ENSEMBLE_SHA
    assert hashlib.sha256((d / "_stack_common.py").read_bytes()).hexdigest()[:16] == attend._STACK_SHA


@pytest.mark.parametrize("line", ["Odo: fine, wanderer.", "**Odo:** fine", "'Odo: fine", '"Odo: fine', "- Odo: fine",
                                  "\u2022 Odo: fine", "> Odo: fine", "\u201cOdo: fine", "  __Odo__: fine"])
def test_l7_tags_tolerate_leading_quote_or_bullet(line):
    """a leading quote/bullet before the tag must not hide a line from the rule checks (session_state 'Nell: miss)"""
    import re
    assert re.search(ar._tagged("Odo"), line, re.M)
    assert not re.search(ar._tagged("Odo"), "Bodo: x\nThe Odo: y", re.M)


# ---------------------------------------------------------------- L12 (interaction: a last-word rule with an exception)
def test_l12_reference_mutants_and_exception():
    seen_exc = 0
    for k in range(10):
        g = _g(12, f"x{k}")
        m = attend.materialize(g, release_seed=RS, case_id=f"X{k}")
        p = attend._plan(g, RS, f"X{k}")
        ref = attend.reference(g, release_seed=RS, case_id=f"X{k}")
        assert grade_weighted(m["checks"], split_turns(ref)) == 1.0
        assert not any(c["id"].startswith("exact-") for c in m["checks"])          # no word counting to the exact
        ex = [c for c in m["checks"] if c["id"].startswith("endx-")]
        assert len(ex) >= 12
        T = len(m["turns"])
        for t in range(1, T + 1):
            for o in p["obl"][t]:
                if o["type"] != "endx":
                    continue
                # break this one obligation: only its own check fails
                bad = ar.compose(p, t, rng("v", o["cid"]), violate=o["cid"])
                out = session_checks.run_checks({"checks": [c for c in m["checks"] if c["turns"] == [t]]}, [""] * (t - 1) + [bad])
                failed = {r["id"] for r in out["results"] if not r.get("pass")}
                assert o["cid"] in failed, o
                plain = ar.compose(p, t, rng("p", o["cid"]), only=[dict(x, type="end") if x is o else x for x in p["obl"][t]])
                out = session_checks.run_checks({"checks": [c for c in m["checks"] if c["id"] == o["cid"]]}, [""] * (t - 1) + [plain])
                if not out["results"][0].get("pass"):
                    seen_exc += 1                     # ignoring the exception fails exactly where it applies
    assert seen_exc >= 10


def test_l12_exception_speaker_is_named_by_the_digit_rule():
    for k in range(10):
        p = attend._plan(_g(12, f"y{k}"), RS, f"Y{k}")
        ex = [r for r in p["rules"] if r["kind"] == "endx"]
        par = [r for r in p["rules"] if r["kind"] == "par"]
        assert ex and par and ex[0]["other"] in (par[0]["odd"], par[0]["even"])


def test_l7_to_l10_instances_unchanged_by_l12():
    """digest of L7-L10 instances (turns, checks, system; 8 keys each). Re-taken after the panel round: the voice
    revoke/change line now says "Any other rule about X that is in force now stays in force" (a blind reader read "My
    other rules still stand" as reviving a scrapped rule), the L7+ voice check takes ensemble's tag lead, three trigger
    sentences name their topic, a tag may follow the opening phrase on its line, and a tally number may sit anywhere in
    the clause with the word 'tally'; the cast voice check reads the same tag as presence (K, after 3acb0fa). The L12
    code itself still leaves L7-L10 alone (the earlier pin, taken on 7945631, held through a91cfbb/7b914ae)."""
    h = hashlib.sha256()
    for lv in (7, 8, 9, 10):
        for k in range(8):
            m = attend.materialize(_g(lv, f"h{k}"), release_seed="4.5.0", case_id=f"H{k}")
            h.update(json.dumps([m["turns"], m["checks"], m["system"]], sort_keys=True).encode())
    assert h.hexdigest() == "acb08a5afef3059b8c81431666a67382aaf1fed6df72b8a4ccf2f7cc771a7e65"


def test_xtopic_fillers_never_mention_a_rule_topic():
    """'is the dog friendly?' under a dog tally / say / latch rule is a mention the schedule did not count (L11, L12)"""
    for lv in (DENSE, 12):
        for k in range(20):
            p = attend._plan(_g(lv, f"f{k}"), RS, f"F{k}")
            used = {r.get("topic") for r in p["rules"]}
            for msg in p["turns"]:
                q = msg.rsplit(", ", 1)[-1]
                if "the dog" in used:
                    assert "dog friendly" not in q
                if "the fire" in used:
                    assert "fireplace" not in q


def test_l7_up_voice_check_sees_a_quoted_or_bulleted_tag():
    """ensemble's _voice gained an opt-in tag lead (lead=True); L7+ use it, so a line "'Odo: ..." or "- Odo: ..." that
    drops the NPC's opener word fails the cast check (it slipped past the plain tag before). Lead off = old patterns."""
    from crucibleforge.gen import ensemble
    assert ensemble._voice("Odo", "Aye") == ensemble._voice("Odo", "Aye", lead=False)
    assert ensemble._cast_check(1, "Wren", {"Nell": "Well"}, {"Odo": "Aye"}) == \
        ensemble._cast_check(1, "Wren", {"Nell": "Well"}, {"Odo": "Aye"}, lead=False)
    for lv in (7, 9, 12):
        g = _g(lv, "q1")
        m = attend.materialize(g, release_seed=RS, case_id="Q1")
        p = attend._plan(g, RS, "Q1")
        ref = split_turns(attend.reference(g, release_seed=RS, case_id="Q1"))
        cast = [c for c in m["checks"] if c["id"] == "cast-t1"]
        assert session_checks.run_checks({"checks": cast}, ref[:1])["results"][0]["pass"]
        n = p["npcs"][0]
        lines = ref[0].splitlines()
        i = next(j for j, ln in enumerate(lines) if ln.startswith(n + ":"))
        for lead in ("'", "- ", "> "):
            bad = list(lines)
            bad[i] = f"{lead}{n}: Hello there, friend."
            out = session_checks.run_checks({"checks": cast}, ["\n".join(bad)])
            assert not out["results"][0]["pass"], (lv, lead)


def test_panel_round_readings_all_pass():
    """Panel 2026-11-14 (three blind Opus readers + Sonnet/Haiku L12 rows): readings the stated rules allow must pass.
    - 'Hush now Dara: ...' (opening phrase before the first tag on one line) is a tagged Dara line;
    - the tally number may sit up to 40 characters from 'tally' in the same clause, on either side;
    - no trigger sentence leaves its topic to inference (kitchen smell, settling what is owed, 'a grey cat')."""
    text = json.dumps(ar.TOPICS)
    for gone in ("smells good from the kitchen", "settle what I owe", "A grey cat"):
        assert gone not in text
    import re
    assert re.search(ar._tagged("Dara"), "Hush now Dara: Right, two autumns.", re.M)
    assert re.search(ar._tagged("Dara"), "Hear ye, Dara: Right.", re.M)
    assert not re.search(ar._tagged("Dara"), "Well Dara: Right.", re.M)
    p = {"npcs": ["Garrick"], "user": "Wren"}
    o = {"type": "tally", "who": "Garrick", "k": 1, "cid": "tally-x"}
    chk = {"id": "tally-x", "fact": "tally-x", "ftype": "fact", "group": "constraint", "turns": [1], "scope": "ic",
           "weight": 1.0, "type": "require_regex", "pattern": ar.pattern(p, o)}
    pad = "\nThe lamps burn low and the rain keeps on against the glass."
    for line, ok in [("Garrick: So, the tally of fire talk stays at one while I say so.", True),
                     ("Garrick: So, one on the tally, neighbour.", True),
                     ("Garrick: So, fire tally: one.", True),
                     ("Garrick: So, the tally is two; one more pint.", False)]:
        out = session_checks.run_checks({"checks": [chk]}, [line + pad])
        assert bool(out["results"][0].get("pass")) == ok, line


def test_cast_voice_sees_a_tag_after_the_opening_phrase():
    """K's review of 3acb0fa: cast tested presence with _tagged (opening phrase allowed before the tag) but voice with
    ensemble's _voice (no opening phrase), so "Hush now Dara: the fire draws well." counted Dara present and was never
    voice-checked. Off-voice after the phrase must fail; in-voice after the phrase passes; plain off-voice fails."""
    import re
    g = _g(12, "p1")
    m = attend.materialize(g, release_seed=RS, case_id="PRB-attend-host-L12-p1")
    p = attend._plan(g, RS, "PRB-attend-host-L12-p1")
    cast = [c for c in m["checks"] if c["id"] == "cast-t1"][0]
    ref = split_turns(attend.reference(g, release_seed=RS, case_id="PRB-attend-host-L12-p1"))[0]
    lines = ref.splitlines()
    n = p["npcs"][0]
    w = p["openers"][n]
    i = next(j for j, ln in enumerate(lines) if re.match(rf"\s*{re.escape(n)}:", ln))

    def passes(new):
        bad = list(lines)
        bad[i] = new
        return session_checks.run_checks({"checks": [cast]}, ["\n".join(bad)])["results"][0].get("pass")

    assert not passes(f"Hush now {n}: the fire draws well tonight, friend.")
    assert passes(f"Hush now {n}: {w}, the fire draws well tonight, friend.")
    assert not passes(f"{n}: the fire draws well tonight, friend.")
