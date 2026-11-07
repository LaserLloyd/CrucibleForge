"""max_repeat: cross-turn word n-gram repetition (session_checks, 2026-11-07).

Synthetic text only."""
from crucibleforge import session_checks as sc
from crucibleforge.verify_cases import _check_session_checks

WORDS = ("amber basil cedar dune ember fjord grove harbor island jetty kelp lagoon meadow "
         "nettle orchard pebble quarry river summit thicket upland valley willow yarrow zephyr "
         "anchor bramble canyon delta estuary fern glacier heath inlet juniper knoll ledge marsh").split()


def _fresh(seed: int, n: int = 40) -> str:
    """n words with no 4-gram shared with another seed (a rotating, strided walk)."""
    k = len(WORDS)
    return " ".join(WORDS[(seed * 7 + i * (seed % 5 + 1)) % k] + str(seed) for i in range(n))


def _run(turns, **opts):
    chk = {"type": "max_repeat", "max_share": 0.2, **opts}
    return sc.CHECKS["max_repeat"](list(enumerate(turns, start=1)), chk)


def test_fresh_turns_pass():
    out = _run([_fresh(i) for i in range(1, 7)])
    assert out[0] is True
    assert "worst" in out[1]


def test_copy_paste_turn_fails():
    base = _fresh(1)
    out = _run([base, _fresh(2), base])          # turn 3 repeats turn 1 whole
    assert out[0] is False
    assert len(out) == 2 or not out[2].get("credit")   # a plain fail, no partial credit
    assert "t3" in out[1]


def test_partial_band_gives_half_credit():
    a = _fresh(1).split()
    # turn 3: the first 10 words of turn 1 (7 of its 37 4-grams repeat, 0.19 of 37)
    # plus fresh words -> share between max_share and fail_share
    t3 = " ".join(a[:12]) + " " + _fresh(3, 30)
    shares = sc.repeat_shares([(1, " ".join(a)), (2, _fresh(2)), (3, t3)], {"n": 4})
    s3 = dict((i, s) for i, s, _ in shares)[3]
    assert 0.2 < s3 <= 0.3
    out = _run([" ".join(a), _fresh(2), t3], max_share=0.2, fail_share=0.3)
    assert out[0] is False and out[2] == {"credit": 0.5}
    assert out[1].startswith("partial:")
    res = sc.run_checks({"checks": [{"id": "rep", "type": "max_repeat", "max_share": 0.2,
                                     "fail_share": 0.3}]}, [" ".join(a), _fresh(2), t3])
    assert res["results"][0]["credit"] == 0.5 and res["rate"] == 0.5


def test_from_turn_skips_early_turns_but_keeps_them_as_reference():
    base = _fresh(1)
    # turn 2 repeats turn 1, but judging starts at turn 3; turn 3 is fresh
    assert _run([base, base, _fresh(3)], from_turn=3)[0] is True
    # turn 4 repeats turn 1, which is before from_turn: still the reference
    assert _run([base, _fresh(2), _fresh(3), base], from_turn=3)[0] is False


def test_short_turns_are_not_judged():
    out = _run(["one two three four five", "one two three four five"])
    assert out[0] is True and "no turn long enough" in out[1]


def test_case_and_punctuation_do_not_hide_a_repeat():
    base = _fresh(1)
    loud = base.upper().replace(" ", ", ")
    assert _run([base, loud])[0] is False


def test_scope_default_ignores_ooc_note():
    base = _fresh(1)
    t2 = _fresh(2) + " ((OOC: " + base + "))"
    assert _run([base, t2])[0] is True
    assert _run([base, t2], scope="all")[0] is False


def test_verify_accepts_and_rejects():
    ok = {"turns": ["a", "b", "c"], "checks": [{"id": "r", "type": "max_repeat", "max_share": 0.15,
                                                "fail_share": 0.25, "n": 4, "from_turn": 2}]}
    assert _check_session_checks(ok) == []
    bad = {"turns": ["a", "b"], "checks": [{"id": "r", "type": "max_repeat", "max_share": 0.4,
                                            "fail_share": 0.3, "from_turn": 5, "turns": [1]}]}
    errs = " ".join(_check_session_checks(bad))
    assert "max_share <= fail_share" in errs and "from_turn" in errs and "drop 'turns'" in errs
    assert "missing max_share" in " ".join(_check_session_checks(
        {"turns": ["a"], "checks": [{"id": "r", "type": "max_repeat"}]}))
