"""G2 tracevm generator: determinism, seeds differ, reference 1.0, mutants < 1.0, lazy 0.0, sizes."""
import hashlib
import json

import pytest

from crucibleforge import graders
from crucibleforge.gen import tracevm as T

G = lambda lv: {"type": "tracevm", "level": lv}  # noqa: E731
KW = lambda i: dict(release_seed="t-rel", case_id=f"TV-{i}")  # noqa: E731
INSTR = {1: (6, 10), 2: (12, 18), 3: (20, 30), 4: (35, 50), 5: (60, 80), 6: (60, 80), 7: (70, 95), 8: (80, 110), 9: (105, 150)}
SPEC_TOK = {1: (600, 900), 2: (600, 900), 3: (600, 900), 4: (600, 900), 5: (600, 900),
            6: (1050, 1180), 7: (1180, 1320), 8: (1300, 1450), 9: (1300, 1650)}
QLV = (6, 7, 8, 9)
GOLDEN_LEGACY = {"tv1-1": "f4df24663ba00543", "tv1-2": "96c1833aca6abc1c", "tv1-3": "45fad965ead567a7",
                 "tv2-1": "be650106a6b52fec", "tv2-2": "e681416b86d016c6", "tv2-3": "05d3429d200dea7a",
                 "tv3-1": "b861b2f5aee06341", "tv3-2": "73b2173cffca2dbb", "tv3-3": "4c268ce8e703e4ba",
                 "tv4-1": "322b062971523e53", "tv4-2": "942011c4b177d8d6", "tv4-3": "0acb131a9ed065d5",
                 "tv5-1": "2e4f6dcf8a444501", "tv5-2": "5f353166f7848453", "tv5-3": "abdb88a1a166fa4d"}


def _eng(text, m):
    return graders.grade_lines_prefix(text, m["grader_config"])["rate"]


@pytest.mark.parametrize("lv", sorted(INSTR))
def test_deterministic_and_seeds_differ(lv):
    a = T.materialize(G(lv), **KW(1))
    assert a == T.materialize(G(lv), **KW(1))
    assert a["prompt"] != T.materialize(G(lv), **KW(2))["prompt"]
    assert a["prompt"] != T.materialize(G(lv), release_seed="x", case_id="TV-1")["prompt"]


@pytest.mark.parametrize("lv", sorted(INSTR))
def test_reference_mutants_lazy_and_engine_agree(lv):
    for i in range(5):
        m = T.materialize(G(lv), **KW(i))
        ref = T.reference(G(lv), **KW(i))
        assert T.grade(ref, m) == 1.0 and _eng(ref, m) == 1.0
        for x in T.mutants(G(lv), **KW(i)):
            assert T.grade(x, m) < 1.0
            # engine regexes may credit a different prefix share only by < one line
            assert _eng(x, m) < 1.0
        for x in ("", "I do not know.", "```text\n42\n```"):
            assert T.grade(x, m) == 0.0 or m["expected"][0] == "42"
            assert _eng(x, m) == T.grade(x, m) or m["expected"][0] == "42"
        # prefix credit: first k lines right, then wrong
        exp = m["expected"]
        k = len(exp) // 2
        part = "```text\n" + "\n".join(exp[:k] + ["zzz"] + exp[k + 1:]) + "\n```"
        assert T.grade(part, m) == pytest.approx(k / len(exp))
        assert _eng(part, m) == pytest.approx(k / len(exp), abs=1e-3)


@pytest.mark.parametrize("lv", sorted(INSTR))
def test_sizes_within_docstring_table(lv):
    lo, hi = INSTR[lv]
    for i in range(30):
        m = T.materialize(G(lv), release_seed="size", case_id=f"S{i}")
        mt = m["meta"]
        assert lo <= mt["n_instructions"] <= hi
        assert 4 <= len(m["expected"]) <= 24
        assert mt["steps_executed"] <= T.STEP_LIMIT
        lo_t, hi_t = SPEC_TOK[lv]
        assert lo_t <= len(m["prompt"]) // 4 <= hi_t, (lv, i, len(m["prompt"]) // 4)
        if lv in QLV:
            d = T.QSIZE[lv]
            assert d["steps"][0] <= mt["steps_executed"] <= d["steps"][1]
            assert d["out"][0] <= len(m["expected"]) <= d["out"][1]


def test_levels_1_to_5_byte_identical_to_the_4_1_generator():
    """G-41Freeze: the legacy levels did not move when levels 6-8 were added."""
    for key, want in GOLDEN_LEGACY.items():
        lv, sd = int(key[2]), int(key[4])
        g = T.generate(sd, lv)
        got = hashlib.sha256(json.dumps([g["spec"], g["expected"], g["meta"]], sort_keys=True).encode()).hexdigest()[:16]
        assert got == want, key


@pytest.mark.parametrize("lv", QLV)
def test_quirk_levels_30_distinct_programs_and_max_tokens(lv):
    progs = set()
    for i in range(30):
        g = T.generate(2000 + i, lv)
        progs.add(g["program"])
    assert len(progs) == 30
    assert T.materialize(G(lv), **KW(0))["max_tokens"] == 6144
    assert T.materialize(G(2), **KW(0))["max_tokens"] == 2048


@pytest.mark.parametrize("lv", QLV)
def test_each_quirk_blind_run_diverges_within_first_40_percent(lv):
    for i in range(8):
        g = T.generate(3000 + i, lv)
        prog = [l.split(": ", 1)[1] for l in g["program"].splitlines()]
        quirks = set(T.QUIRK_LEVELS[lv])
        out = T.run_q(prog, quirks)[0]
        assert out == g["expected"]
        assert set(g["blind"]) == quirks
        muts = T.mutants(G(lv), release_seed="t-rel", case_id=f"TV-{i}")   # a different instance: just the shape
        for q in quirks:
            blind = T.run_q(prog, quirks - {q})[0]
            d = T.first_divergence(out, blind)
            assert d is not None and d < 0.4 * len(out), (lv, i, q, d, len(out))
            assert blind == g["blind"][q]
        assert len(muts) == 4 + len(quirks)


@pytest.mark.parametrize("lv", QLV)
def test_quirk_blind_mutants_score_below_one_in_both_graders(lv):
    for i in range(4):
        m = T.materialize(G(lv), **KW(i))
        muts = T.mutants(G(lv), **KW(i))
        assert len(muts) >= 4 + len(T.QUIRK_LEVELS[lv]) - 1          # (blind outputs can coincide with a shorter slip)
        for x in muts:
            assert T.grade(x, m) < 1.0 and _eng(x, m) < 1.0
        blind_texts = muts[4:]
        for x in blind_texts:
            assert T.grade(x, m) < 0.4 + 1e-9            # prefix credit: diverges before 40% of the lines


@pytest.mark.parametrize("q", sorted(T.QUIRK_EXAMPLES))
def test_spec_micro_examples_are_right_under_the_reference_vm(q):
    """Every worked example printed in the spec is run on the reference VM, and removing the quirk changes it."""
    prog, want = T.QUIRK_EXAMPLES[q]
    allq = {"delay", "sat", "signed", "memwrap"}
    assert T.run_q(prog, allq)[0] == want
    assert T.run_q(prog, allq - {q})[0] != want
    lv = 8
    spec = T.materialize(G(lv), **KW(0))["prompt"]
    assert "\n".join(f"    {i}: {l}" for i, l in enumerate(prog)) in spec
    assert "prints: " + ", ".join(want) in spec


@pytest.mark.parametrize("lv", QLV)
def test_delay_slot_structure(lv):
    for i in range(10):
        prog = [l.split(": ", 1)[1].split() for l in T.generate(4000 + i, lv)["program"].splitlines()]
        jumps = [k for k, ins in enumerate(prog) if ins[0] in ("jz", "jnz", "jlt")]
        for k in jumps:
            assert k + 1 < len(prog)                                  # a slot line exists
            assert prog[k + 1][0] not in ("jz", "jnz", "jlt", "halt")  # never a jump (or halt) in a slot
        if lv >= 7:                                                    # a data-dependent loop exits on jlt r2 r3
            n_dl = sum(prog[k][:3] == ["jlt", "r2", "r3"] and int(prog[k][3]) < k for k in jumps)
            assert n_dl >= (3 if lv == 9 else 1)
        if lv >= 8:
            assert {"ld", "st"} <= {ins[0] for ins in prog}


def test_reference_vm_slot_and_step_count():
    prog = ["set r0 1", "jnz r0 4", "out r0", "out r0", "halt"]
    out, steps, taken, ok = T.run_q(prog, {"delay"})
    assert (out, steps, taken, ok) == (["1"], 4, 1, True)         # set, jnz, slot out, halt
    out, steps, _, _ = T.run_q(prog, set())
    assert (out, steps) == ([], 3)
