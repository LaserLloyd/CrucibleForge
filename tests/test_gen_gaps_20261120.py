"""GAP packs (patchgap, toolgap, everygap): reference 1.0, planted wrong answers fail, staged sets stay out of discovery,
the fix-checker stamp is untouched. Scratch under /var/tmp only."""
import hashlib
import json
from pathlib import Path

import pytest

from crucibleforge import fixrows45, graders, ladder
from crucibleforge.gen import everygap as eg
from crucibleforge.gen import patchgap as pg
from crucibleforge.gen import toolgap as tg

ROOT = Path(__file__).resolve().parents[1]
SEED = "4.5.0"


# ------------------------------------------------------------------------------------------- toolgap
@pytest.mark.parametrize("level", [1, 2, 3])
def test_toolgap_reference_and_wrong_policies(level):
    for k in range(12):
        key = f"L{level}:t{k}"
        ref = tg.console_result(tg.run_policy(tg.new_session(key), "reference"))
        assert ref["score"] == 1.0, (key, ref["detail"])
        bit = 0
        for p in tg.POLICIES[1:]:
            if tg.console_result(tg.run_policy(tg.new_session(key), p))["score"] < 1.0:
                bit += 1
        assert bit >= {1: 6, 2: 8, 3: 9}[level]
    assert tg.console_result(tg.new_session(f"L{level}:t0"))["score"] < 1.0      # doing nothing


def test_toolgap_family_contract():
    g = {"type": "toolgap", "level": 2, "key": "x"}
    a = tg.materialize(g, release_seed=SEED, case_id="T1")
    assert a == tg.materialize(g, release_seed=SEED, case_id="T1")
    assert tg.grade(tg.reference(g, release_seed=SEED, case_id="T1"), a) == 1.0
    assert tg.grade("", a) == 0.0
    for m in tg.mutants(g, release_seed=SEED, case_id="T1"):
        assert tg.grade(m, a) < 1.0


def test_toolgap_traps_are_real():
    I = tg.instance("L3:t1")
    w = tg.World(I)
    oid = next(o for o, x in I.orders.items() if x["kind"] == "split")
    s, q = I.orders[oid]["lines"][0]
    r = w.call("reserve", {"order_id": oid, "sku": s, "qty": q, "location": "A"})
    assert r["ok"] is True and r["reserved"] < q                   # ok, yet less than asked
    assert tg.World(I).call("list_orders", {})["next_cursor"]       # levels 2 and 3 page
    assert r["expires_after_calls"] == 14                           # level 3 reservations lapse


# ------------------------------------------------------------------------------------------- everygap
@pytest.mark.parametrize("level", [1, 2])
def test_everygap_reference_and_mutants(level):
    for k in range(25):
        g = {"type": "everygap", "level": level, "key": str(k)}
        m = eg.materialize(g, release_seed=SEED, case_id=f"T{k}")
        assert graders.grade_checks(eg.reference(g, release_seed=SEED, case_id=f"T{k}"), m["grader_config"])["grade"] == "pass"
        for mu in eg.mutants(g, release_seed=SEED, case_id=f"T{k}"):
            assert graders.grade_checks(mu, m["grader_config"])["grade"] != "pass"


def test_everygap_amendment_is_net():
    g = {"type": "everygap", "level": 2, "key": "1"}
    m = eg.materialize(g, release_seed=SEED, case_id="T1")
    net, specs = m["meta"]["net"], m["meta"]["specs"]
    assert net["word"] == specs[1]["word"] != specs[0]["word"] and net["uniform"] and net["kwline"] != specs[0]["kwline"]


# ------------------------------------------------------------------------------------------- patchgap (sandbox)
def _bwrap():
    from crucibleforge import graders as g
    return bool(g._BWRAP and g.probe_bwrap(g._BWRAP)[0])


@pytest.mark.skipif(not _bwrap(), reason="fixcheck needs bwrap")
@pytest.mark.parametrize("level", [1, 2])
def test_patchgap_floors(level):
    from crucibleforge import fixcheck
    g = {"type": "patchgap", "level": level, "key": "t1"}
    cid = f"T-pg-{level}"
    m = pg.materialize(g, release_seed=SEED, case_id=cid)
    cfg = m["grader_config"]
    me = m["meta"]
    assert len(me["lying"]) >= 3 and len(me["decoys"]) >= 2
    assert "intended:" in m["prompt"] and "TODO(review)" in m["prompt"]
    assert fixcheck.grade_answer(pg.reference(g, release_seed=SEED, case_id=cid), cfg)["score"] == 1.0
    assert fixcheck.grade_answer("no edits", cfg)["score"] == 0.0
    given = "\n".join(f"{p}\n```python\n{t}```\n" for p, t in sorted(cfg["files"].items()) if p.endswith(".py"))
    assert fixcheck.grade_answer(given, cfg)["score"] == 0.0
    for mu in pg.mutants(g, release_seed=SEED, case_id=cid):         # an over-eager 'fix' of a decoy
        assert fixcheck.grade_answer(mu, cfg)["score"] <= 0.5


# ------------------------------------------------------------------------------------------- wiring / stamps
# The staged Fixes set (PATCHGAP, former work/c3_fix/set-1) left cases/ladder on 2026-11-20, as the PROBE draft did
# on 2026-11-15: every process that loads cases materialized it (>145 s). The generator, its grader-stamp fixture
# entry and its marks rows stay; the spec is recoverable from git (f6010e0:cases/ladder/work/c3_fix/set-1.json).
PATCHGAP_SET = ROOT / "cases/ladder/work/c3_fix/set-1.json"


def test_set_files_shape():
    for f in ("work/c5_tools/set-3", "chat/h6_know/set-3"):
        spec = json.loads((ROOT / "cases/ladder" / f"{f}.json").read_text(encoding="utf-8"))
        assert spec["points"] == 1.0 and spec["generator"]["family"]
        assert "status" not in spec


def test_patchgap_is_out_of_the_load_path_but_its_generator_stays():
    from crucibleforge import gen
    assert not PATCHGAP_SET.exists()
    assert not [c for c in ladder.inline_cases() if (c.get("generator") or {}).get("type") == "patchgap"]
    gen.discover()
    assert "patchgap" in gen.families()
    pinned = json.loads((ROOT / "tests/fixtures/grader_stamps_8f946f9.json").read_text(encoding="utf-8"))
    assert "patchgap" in pinned


def test_fix_checker_stamp_unchanged_by_this_lane():
    pinned = json.loads((ROOT / "tests/fixtures/grader_stamps_8f946f9.json").read_text(encoding="utf-8"))
    assert isinstance(pinned, dict)
    files = ("fixcheck.py", "fixspecs.py", "codebank.py", "gen/_patchbase.py", "graders.py")
    h = hashlib.sha256()
    for f in files:
        h.update(f.encode() + b"\0" + (ROOT / "crucibleforge" / f).read_bytes() + b"\0")
    assert h.hexdigest()[:12] == fixrows45.checker_stamp()
    assert "patchgap" not in fixrows45.FIX_FAMILIES            # staged: not a counted Fixes unit


def test_new_families_in_no_standard_composition():
    txt = (ROOT / "profiles/bench45.yaml").read_text(encoding="utf-8")
    for fam in ("patchgap", "everygap"):
        assert fam not in txt
    # step 2 (2026-11-21, CRITICAL-PATH-45): toolgap L1-2 joined Tools in the standard chain work/c5_toolgen, only there
    import yaml
    std = yaml.safe_load(txt)["compositions"]["standard"]
    fams = {k: [e["family"] for e in (v or {}).get("flat") or []] for k, v in std["chains"].items()}
    assert [k for k, f in fams.items() if "toolgap" in f] == ["work/c5_toolgen"]


# ------------------------------------------------------------------------------------------- wired sets
def test_wired_sets_are_live_and_fixes_stays_staged():
    for f in ("work/c5_tools/set-3", "chat/h6_know/set-3"):
        assert not ladder.is_staged(json.loads((ROOT / "cases/ladder" / f"{f}.json").read_text(encoding="utf-8"))), f
    assert not PATCHGAP_SET.exists()                         # Fixes: not wired, and not even staged in the tree


def test_scorer_stamp_reads_no_ladder_file():
    from crucibleforge import segments45
    assert not [f for f in segments45.SCORER_FILES if "ladder" in f or f.endswith(".json")]


def test_toolgap_runs_through_the_runner_console_loop(monkeypatch):
    from crucibleforge import runner
    from crucibleforge.api import ChatResult
    monkeypatch.setattr(runner, "row_budget", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_prompt_tokens", lambda m: 10)
    g = {"type": "toolgap", "level": 2, "key": "r1"}
    m = tg.materialize(g, release_seed=SEED, case_id="T-run")
    case = {"id": "TOOLGAP-r2-1", "category": "coding", "generator": g, **m}
    reps = iter(tg.reference(g, release_seed=SEED, case_id="T-run").split("\n<<<TURN>>>\n"))

    class Ctx:
        def budget(self, k):
            return k

        def recover_for(self, c):
            return None

        def call(self, messages, **kw):
            return ChatResult(response_text=next(reps, ""), reasoning_text="", finish_reason="stop", tool_calls=[],
                              prompt_tokens=10, completion_tokens=5, reasoning_tokens=0)
    row = runner._run_console_loop(case, {"case_id": case["id"]}, Ctx(), 512, None, None)
    assert float(row.get("score", row.get("rate", 0))) == 1.0, {k: row.get(k) for k in ("score", "rate", "grade")}
