"""Margins isolated from pack sets (stamp-2 item B): one seeded stream per stratum (and per case id in
report._gen_draws), so adding or removing a pack set never moves another stratum's draws."""
from __future__ import annotations

from crucibleforge import headline as h
from crucibleforge import report, segments45

STD = {(1, "code"): ["a", "b", "c", "d"], (1, "reason"): ["x", "y", "z"]}
PACK = {(3, "work/pk"): ["p1", "p2", "p3"]}


def _per_ids(draws, ids):
    return [{i: d.get(i) for i in ids} for d in draws]


def test_pack_stratum_leaves_standard_draws_identical():
    base = h.draws(STD, 200, 11, per_stratum=True)
    both = h.draws({**STD, **PACK}, 200, 11, per_stratum=True)
    ids = [i for v in STD.values() for i in v]
    assert _per_ids(base, ids) == _per_ids(both, ids)
    assert any(i in d for d in both for i in ("p1", "p2", "p3"))


def test_one_id_in_stratum_a_leaves_stratum_b_identical():
    a = h.draws(STD, 200, 11, per_stratum=True)
    st2 = {**STD, (1, "code"): ["a", "b", "c", "d", "e"]}
    b = h.draws(st2, 200, 11, per_stratum=True)
    assert _per_ids(a, ["x", "y", "z"]) == _per_ids(b, ["x", "y", "z"])
    st3 = {**STD, (1, "code"): ["a", "b", "c"]}
    c = h.draws(st3, 200, 11, per_stratum=True)
    assert _per_ids(a, ["x", "y", "z"]) == _per_ids(c, ["x", "y", "z"])


def test_gen_draws_per_case_id_isolated():
    s1 = {(1, "a"): ["a", "b"], (1, "b"): ["c"]}
    s2 = {(1, "a"): ["a", "b"], (1, "b"): ["c"], (3, "w"): ["q"]}
    d1, d2 = report._gen_draws(s1, 100, 5, per_stratum=True), report._gen_draws(s2, 100, 5, per_stratum=True)
    assert [{k: d[k] for k in "abc"} for d in d1] == [{k: d[k] for k in "abc"} for d in d2]
    s3 = {(1, "a"): ["a"], (1, "b"): ["c"]}
    d3 = report._gen_draws(s3, 100, 5, per_stratum=True)
    assert [d["c"] for d in d1] == [d["c"] for d in d3]
    assert [d["a"] for d in d1] == [d["a"] for d in d3]


def test_pairing_and_prefix_stability():
    # two models read the same strata -> identical draws (pairing); and n-prefix stable
    assert h.draws({**STD, **PACK}, 300, 3, per_stratum=True) == h.draws({**STD, **PACK}, 300, 3, per_stratum=True)
    assert h.draws(STD, 100, 3, per_stratum=True) == h.draws(STD, 300, 3, per_stratum=True)[:100]
    assert report._gen_draws(STD, 50, 3, per_stratum=True) == report._gen_draws(STD, 120, 3, per_stratum=True)[:50]
    # shape: every resample draws len(ids) copies per stratum, each with a 32-bit seed
    for m in h.draws(STD, 20, 3, per_stratum=True):
        assert sum(len(v) for v in m.values()) == 7
        assert all(0 <= s < 2 ** 32 for v in m.values() for s in v)


MARGIN_WRITERS = ("headline.py", "segments45.py", "nsfwplus45.py", "rplong_mean.py", "packs45.py",
                  "scoring45.py", "tiers.py", "scale.py", "agree.py", "lineage.py", "assistant45.py",
                  "agree_marks.py", "rplong_marks.py", "marksinput45.py", "resolution45.py")
MARK_SCRIPTS = ("scripts/check_pack_marks45.py", "scripts/check_marks45.py", "scripts/check_rplong_marks45.py",
                "scripts/check_judge_marks.py", "scripts/check_classing_marks.py", "scripts/check_assistant_marks.py",
                "scripts/resolution45.py")


def test_stamp_inputs_hold_every_margin_writer():
    """A restated list (a module that computes points, margins, separation or a mark result), plus: the variance
    draws live in headline.py, not in the unstamped report.py."""
    for f in MARGIN_WRITERS:
        assert f in segments45.SCORER_FILES, f
    for f in MARK_SCRIPTS:
        assert f in segments45.SCORER_DATA, f
    assert hasattr(h, "gen_draws") and report._gen_draws is h.gen_draws


# ---- stream mode: the 4.1 board keeps the legacy single shared stream bit for bit (lead ruling 2026-11-20)
def _legacy_draws_41e3172(st, n, seed):
    """A copy of headline.draws and report._gen_draws as the 41e3172 implementation had them."""
    import random
    rnd = random.Random(seed)
    out = []
    ks = sorted(st)
    for _ in range(n):
        draw = {}
        for k in ks:
            ids = st[k]
            for _ in ids:
                cid = ids[rnd.randrange(len(ids))]
                draw.setdefault(cid, []).append(rnd.getrandbits(32))
        out.append(draw)
    return out


def _legacy_gen_41e3172(st, n, seed):
    import random
    rnd = random.Random(seed + 7)
    ids = sorted({i for v in st.values() for i in v})
    return [{cid: [rnd.getrandbits(32)] for cid in ids} for _ in range(n)]


def test_legacy_mode_is_the_default_and_reproduces_41e3172_exactly():
    sets = [STD, {**STD, **PACK}, {(1, "a"): ["a", "b"], (1, "b"): ["c"], (2, "k"): ["q", "r", "s", "t"]}]
    for st in sets:
        for seed in (3, 11, 424242):
            assert h.draws(st, 150, seed) == _legacy_draws_41e3172(st, 150, seed)
            assert h.draws(st, 150, seed, per_stratum=False) == _legacy_draws_41e3172(st, 150, seed)
            assert report._gen_draws(st, 80, seed) == _legacy_gen_41e3172(st, 80, seed)
    assert h.draws(STD, 50, 3, per_stratum=True) != _legacy_draws_41e3172(STD, 50, 3)


def test_a_4_5_render_selects_per_stratum_mode_and_a_4_1_render_the_legacy_one(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "standard")
    assert h.per_stratum({"segments45": {"code": {}}}) is True
    assert h.per_stratum({"name": "bench"}) is False
    assert h.per_stratum(None) is False
    from crucibleforge import profiles
    assert h.per_stratum(profiles.load_profile("bench45")) is True
    assert h.per_stratum(profiles.load_profile("bench")) is False


def test_every_draw_caller_passes_the_mode():
    import re
    from pathlib import Path
    root = Path(report.__file__).parent
    for f in ("report.py", "resolution45.py"):
        src = (root / f).read_text(encoding="utf-8")
        for m in re.finditer(r"(?:_h|H)\.draws\([^)]*\)|_gen_draws\(st[^)]*\)", src):
            assert "per_stratum" in m.group(0), (f, m.group(0))
