"""NSFW+ packs scoring side, fix pass of the independent review (2026-11-21).

Synthetic rows, fake readers and synthetic side-car fixtures only: no model call. Items are referred to by id only."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import test_nsfw_packs45_scoring_20261120 as T  # noqa: E402
from test_nsfw_packs45_scoring_20261120 import (LABEL, PARTS, fake_readers, pack_rows,  # noqa: E402,F401
                                                std_rows, write_folder)

from crucibleforge import ladder  # noqa: E402
from crucibleforge import nsfwpack45 as NP  # noqa: E402

pytestmark = pytest.mark.live_ladder
MARKS = ROOT / "calibration" / "nsfw-packs-marks-45-addendum-20261120.json"


def _prof():
    from crucibleforge import profiles
    return profiles.load_profile("bench45")


def _attest():
    spec = importlib.util.spec_from_file_location("attest_check_fp", ROOT / "scripts" / "attest_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _read(res, extra=()):
    import read45
    return read45.main(["--results", str(res), "--cap-usd", "1", "--pack-cap-usd", "1", *extra])


def _judge(res):
    """Part 1 (2026-11-21): the run's judge classes the pack units, one vote each (a fake SOFTENED judge, no call)."""
    from crucibleforge import judge45 as J
    from test_judge45_part1_20261121 import FakeJudge
    J.judge_model(res, LABEL, _prof(), J.judge_reader(J.judge_id(_prof())), FakeJudge(cls="SOFTENED"))
    return 0


# ================================================================== must-fix 1: the board reads the stored verdicts
def test_the_board_reads_every_stored_pack_verdict(tmp_path, fake_readers):
    rows = pack_rows()
    res = write_folder(tmp_path / "f", rows)
    assert _judge(res) == 0         # one local judge (Part 1); the reader store is no longer read
    from crucibleforge import judge45 as J
    stored = {x["unit_id"] for x in J.JudgeStore(J.store_path(res, LABEL)).lines if x["kind"] == "class"}
    classed = [u for u in NP.units_from_rows(rows) if u.get("classing")]
    assert stored == {u["id"] for u in classed}
    blk = NP.report_block(rows, LABEL, res, _prof(), None)
    assert blk["verdicts"] == len(classed)                  # every stored verdict read, none missed
    for pk in ("pk_erotica", "pk_range", "pk_willing"):     # the fake readers say SOFTENED: the gate zeroes them
        assert blk["shares"][pk]["state"] == "scored" and blk["shares"][pk]["share"] == 0.0, (pk, blk["shares"][pk])


def test_the_marks_script_reads_every_stored_pack_verdict(tmp_path, fake_readers):
    import check_nsfw_pack_marks45 as K
    rows = pack_rows()
    res = write_folder(tmp_path / "f", rows)
    assert _judge(res) == 0         # one local judge (Part 1)
    label, d = K._folder_data(res, _prof())
    assert label == LABEL
    for pk in ("pk_erotica", "pk_range", "pk_willing"):
        assert d["shares"][pk] == 0.0, (pk, d["shares"][pk])


# ================================================================== must-fix 2: un-staged pack sets stay off the old tree
@pytest.fixture
def unstaged(tmp_path, monkeypatch):
    lad = tmp_path / "ladder"
    shutil.copytree(ladder.LADDER_DIR, lad)
    for part in PARTS.values():
        f = lad / "nsfw" / part / "set-1.json"
        spec = json.loads(f.read_text(encoding="utf-8"))
        spec.pop("status", None)
        f.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    monkeypatch.setattr(ladder, "LADDER_DIR", lad)
    from crucibleforge import profiles
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "standard")     # the standard tree (its parts hold the six sets)
    return lad


def _pack_ids():
    return {r["case_id"] for r in pack_rows()}


def test_unstaged_pack_sets_are_discovered_but_never_a_board_set(unstaged):
    from crucibleforge import report as R
    prof = _prof()
    found = {c for seq in ladder.discover(prof, staged=False).values() for s in seq for c in s["cases"]}
    assert found & _pack_ids()                                 # un-staged: the run path sees them
    board = {c for seq in R._ladder_sets(prof).values() for s in seq for c in s["cases"]}
    assert not board & _pack_ids()                             # the old tree, headroom and headline never do
    assert not set(R._ladder_levels(prof)) & _pack_ids()


def test_unstaged_pack_rows_reach_no_tier(unstaged):
    from crucibleforge import report as R
    prof = _prof()
    rows = std_rows() + pack_rows()
    by_t = R.tier_rows(prof, rows, None)
    assert not {r["case_id"] for rs in by_t.values() for r in rs} & _pack_ids()
    assert R.tier_rows(prof, std_rows(), None) == by_t


def test_unstaged_pack_cases_are_no_headline_stratum(unstaged):
    from crucibleforge import headline as H
    prof = _prof()
    st = H.strata(prof, {}, None)
    assert {c for ids in st.values() for c in ids}                # strata exist (the check is not vacuous)
    assert not {c for ids in st.values() for c in ids} & _pack_ids()


def test_the_set_predicate_is_the_row_predicate():
    for c in _pack_ids():
        assert NP.is_pack_case(c) and NP.is_pack_row({"case_id": c})
        assert NP.is_pack_set({"cases": [c]})
    assert not NP.is_pack_set({"cases": [r["case_id"] for r in std_rows()]})


# ================================================================== should-fix 3: a cut pack unit is left out
def _cut(u):
    return {**u, "status": {**(u.get("status") or {}), "cut": True, "disposition": "left_out"}}


def test_a_cut_pack_unit_is_left_out_not_zero():
    units = NP.units_from_rows(pack_rows(("pk_range",)))
    v = T._verdicts(units)
    one = [(_cut(u) if u["case_id"].startswith("PKT-r5-") else u) for u in units]
    sh = NP.pack_shares(one, v)["pk_range"]
    assert sh["share"] == pytest.approx(1.0) and sh["outcomes"].get("cut") == 1


def test_a_pack_with_too_many_cut_units_is_not_measured():
    units = NP.units_from_rows(pack_rows(("pk_range",)))
    v = T._verdicts(units)
    cut = [(_cut(u) if not u["case_id"].startswith("PKT-r1-") else u) for u in units]
    sh = NP.pack_shares(cut, v)["pk_range"]
    assert sh["share"] is None and sh["state"] == "pending" and "not measured" in sh["reason"]


# ================================================================== should-fix 4: the gate is the PART the pack extends
def _numbers_parts(seg_share=0.95, written=0.95, u1=0.95):
    n = T._numbers(seg_share)
    n["nsfw"]["segments"]["erotica"]["parts"] = {"erotica_written": 2.5 * written, "erotica_dwell": 1.5 * seg_share}
    n["uncensored"]["segments"]["willingness"]["parts"] = {"u1": 3.0 * u1, "dark": 1.0 * seg_share,
                                                          "u2": 1.25 * seg_share, "u4": 0.75 * seg_share}
    return n


def test_erotica_and_willingness_packs_gate_on_their_own_part():
    assert NP.segment_gate(_numbers_parts(0.95, written=0.5), "pk_erotica") == pytest.approx(0.5)
    assert NP.segment_gate(_numbers_parts(0.5, written=0.95), "pk_erotica") == pytest.approx(0.95)
    assert NP.segment_gate(_numbers_parts(0.95, u1=0.4), "pk_willing") == pytest.approx(0.4)
    n = _numbers_parts(0.95)
    n["nsfw"]["segments"]["erotica"]["parts"]["erotica_written"] = None
    assert NP.segment_gate(n, "pk_erotica") is None          # part not measured: locked, never a guess
    assert NP.segment_gate(_numbers_parts(0.91), "pk_range") == pytest.approx(0.91)


# ================================================================== should-fix 5: a renamed or removed card
def _gone(rows):
    out = []
    for r in rows:
        if r["case_id"].startswith("PKT-r1-") and r.get("judge_card"):
            r = dict(r, judge_card={**r["judge_card"], "card": "PK-R-99-gone"})
        out.append(r)
    return out


def test_a_removed_pack_card_is_pending_never_a_crash(tmp_path, fake_readers):
    rows = _gone(pack_rows(("pk_range",)))
    units = NP.units_from_rows(rows)
    gone = [u for u in units if u["card"] == "PK-R-99-gone"]
    assert gone and all(NP.unit_score(u, {"cls": "EXPLICIT"})["score"] is None for u in gone)
    sh = NP.pack_shares(units, T._verdicts(units))["pk_range"]
    assert sh["state"] == "pending" and "PK-R-99-gone" in sh["reason"]
    res = write_folder(tmp_path / "f", std_rows() + rows)
    assert _read(res) == 0                                  # the standard read is not stopped
    assert (res / "readers45" / f"{LABEL}.jsonl").is_file()
    import check_nsfw_pack_marks45 as K
    K._folder_data(res, _prof())                            # no crash


# ================================================================== should-fix 6: evaluators as the marks are written
def test_author_check_does_not_fire_without_both_authors_as_takers():
    import check_nsfw_pack_marks45 as K
    items = {"a1": "deepseek", "b1": "minimax"}
    one = {"deepseek-flash": {"a1": 1.0, "b1": 0.2}}
    assert K.author_check(items, one)["status"] == "pass"
    assert K.author_check({"a1": "deepseek", "x": None}, one)["status"] == "not_evaluable"   # an item without author


def test_knowledge_item_scores_are_keyed_by_bank_item():
    import check_nsfw_pack_marks45 as K
    for u in NP.units_from_rows(pack_rows(("pk_know",))):
        sc = K.item_scores(u, None)
        ids = {it["id"] for it in u["grader_config"]["items"]}
        assert sc and set(sc) == ids, (set(sc), ids)
        assert all(v == pytest.approx(1.0) for v in sc.values())     # reference replies


def test_checkable_needs_a_fact_check_or_key_per_item():
    import check_nsfw_pack_marks45 as K
    guard_only = {"checks": [{"id": "len", "type": "word_range"}, {"id": "refusal@1", "type": "refusal_phrase"}]}
    assert K.checkable_case("taboo", guard_only)
    assert K.checkable_case("taboo", {"checks": guard_only["checks"] + [{"id": "anat-1", "type": "require_regex"}]}) is None
    assert K.checkable_case("bodies", {"checks": [{"id": "anat-1", "type": "require_regex"}]})   # bodies needs its key
    assert K.checkable_case("bodies", {"checks": [{"id": "key", "type": "sentence_key"}]}) is None


def test_act_keys_must_be_pack_sex_entries_of_the_addendum(monkeypatch):
    import check_nsfw_pack_marks45 as K
    from crucibleforge import actkeys45
    assert K.act_keys("pk_range")["status"] == "pass"
    monkeypatch.setattr(actkeys45, "acts_for", lambda c: {"explicit-sex": "penetration"})
    assert K.act_keys("pk_range")["status"] == "fail"


def test_first_pack_reads_the_perfect_reference_not_proof_both_ways():
    import check_nsfw_pack_marks45 as K
    runs = {"w1": [0.8, 0.82], "w2": [0.7, 0.6], "w3": [0.5, 0.55]}
    assert K.reference_perfect("pk_range")["status"] == "pass"
    assert K.reference_perfect("pk_roleplay")["status"] == "not_evaluable"     # no reference reply in the bank
    assert K.first_pack(runs, {"w1", "w2", "w3"}, {"status": "pass"})["status"] == "pass"


def test_proof_both_ways_reports_a_real_error_as_a_fail(monkeypatch):
    import check_nsfw_pack_marks45 as K
    from crucibleforge.gen import nsfwpack45 as G

    def boom(*a, **k):
        raise RuntimeError("grader broke")
    monkeypatch.setattr(G, "grade", boom)
    r = K.proof_both_ways("pk_range")
    assert r["status"] == "fail" and "RuntimeError" in r["reason"]


def test_the_thresholds_are_the_addendums_numbers():
    import check_nsfw_pack_marks45 as K
    body = json.loads(MARKS.read_text(encoding="utf-8"))
    adm, fp = body["design_targets"]["admission_rule"], body["design_targets"]["first_pack"]
    assert f"{K.KEEP_REACH:.2f}" in adm and f"{K.FIRST_PACK_BELOW:.2f}" in fp
    assert f"at least {K.FIRST_PACK_MODELS} willing models" in fp and f"{K.RUNS} runs each" in fp
    assert f"above {K.AUTHOR_GAP:.2f}" in body["author_check"]["rule"]
    assert K.AUTHORS == tuple(body["author_check"]["authors"])


# ================================================================== should-fix 7: stamp inputs
def test_the_attest_gate_is_a_stamp_input_and_ladder_stays_out():
    from crucibleforge import segments45
    assert "scripts/attest_check.py" in segments45.SCORER_DATA
    assert not [f for f in segments45.SCORER_FILES if "ladder" in f]
    assert "report.py" not in segments45.SCORER_FILES


# ================================================================== new 5: side-car attestation of reused bank items
KNOW_BANK = "uncensored/unc-know-bank.json"


def _know_items():
    import check_nsfw_pack_marks45 as K
    ids = []
    for _c, m in K._know_cases():
        ids += [i for i in (m.get("meta") or {}).get("items") or [] if i not in ids]
    return ids


def _sidecar(d: Path, bank=KNOW_BANK, drop=None):
    ac = _attest()
    items = {it["id"]: it for it in json.loads((d / KNOW_BANK).read_text(encoding="utf-8"))["items"]}
    recs = {}
    for i in _know_items():
        att = [{"by": w, "model": "m", "date": "2026-11-21", "verdict": "PASS", "reasons": "ok"}
               for w in ("grok", "deepseek") if w != drop]
        recs[i] = {"item_sha": ac.item_sha(items[i]), "attest": att}
    (d / "packs45" / "attest-unc-know.json").write_text(
        json.dumps({"about": "synthetic fixture", "bank": bank, "items": recs}), encoding="utf-8")


@pytest.fixture
def nsfw_copy(tmp_path):
    d = tmp_path / "nsfw"
    shutil.copytree(ROOT / "cases" / "ladder" / "nsfw", d)
    sf = d / "u3_know" / "set-1.json"
    spec = json.loads(sf.read_text(encoding="utf-8"))
    spec.pop("status", None)
    sf.write_text(json.dumps(spec), encoding="utf-8")
    return d


def test_unstaging_the_knowledge_pack_without_side_car_records_is_refused(nsfw_copy):
    (nsfw_copy / "packs45" / "attest-unc-know.json").unlink(missing_ok=True)     # the state named: no side-car
    probs = _attest().problems(nsfw_copy)
    assert any("u3_know" in p and "side-car" in p for p in probs), probs


def test_a_matching_side_car_record_attests_a_reused_item(nsfw_copy):
    _sidecar(nsfw_copy)
    assert _attest().problems(nsfw_copy) == []


def test_an_edited_bank_item_no_longer_matches_its_side_car(nsfw_copy):
    _sidecar(nsfw_copy)
    bank = nsfw_copy / KNOW_BANK
    body = json.loads(bank.read_text(encoding="utf-8"))
    first = _know_items()[0]
    for it in body["items"]:
        if it["id"] == first:
            it["q"] = str(it.get("q")) + " (edited)"
    bank.write_text(json.dumps(body), encoding="utf-8")
    probs = _attest().problems(nsfw_copy)
    assert any(first in p and "item_sha" in p for p in probs), probs


@pytest.mark.parametrize("who", ["grok", "deepseek"])
def test_a_side_car_record_without_both_passes_fails(nsfw_copy, who):
    _sidecar(nsfw_copy, drop=who)
    probs = _attest().problems(nsfw_copy)
    assert any(f"no {who} attestation" in p for p in probs), probs


def test_a_side_car_record_never_attests_an_item_of_another_bank(nsfw_copy):
    _sidecar(nsfw_copy, bank="uncensored/other-bank.json")
    probs = _attest().problems(nsfw_copy)
    assert any("side-car" in p for p in probs), probs
    ac = _attest()
    item = json.loads((nsfw_copy / KNOW_BANK).read_text(encoding="utf-8"))["items"][0]
    side = {"uncensored/other-bank.json": {item["id"]: {"item_sha": ac.item_sha(item), "attest": [
        {"by": w, "date": "2026-11-21", "verdict": "PASS"} for w in ("grok", "deepseek")]}}}
    assert ac.item_attest_problems(item, KNOW_BANK, side)
    assert ac.item_attest_problems(item, "uncensored/other-bank.json", side) == []


def test_the_attestation_mark_reads_the_side_car(tmp_path, monkeypatch):
    import check_nsfw_pack_marks45 as K
    lad = tmp_path / "ladder"
    shutil.copytree(ladder.LADDER_DIR, lad)
    monkeypatch.setattr(ladder, "LADDER_DIR", lad)
    (lad / "nsfw" / "packs45" / "attest-unc-know.json").unlink(missing_ok=True)   # no side-car: not attested
    assert K.attestation("pk_know")["status"] == "fail"
    _sidecar(lad / "nsfw")
    assert K.attestation("pk_know")["status"] == "pass"
    _sidecar(lad / "nsfw", drop="grok")
    assert K.attestation("pk_know")["status"] == "fail"


def test_item_sha_is_sha256_of_canonical_json():
    ac = _attest()
    it = {"b": 1, "a": "é"}
    want = hashlib.sha256(json.dumps(it, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()
    assert ac.item_sha(it) == want


# ================================================================== must-fix 2 follow-up: the pack column keeps its rows
def test_the_pack_column_reads_the_pack_rows_the_board_filter_drops(tmp_path, monkeypatch):
    """report._pack_rows_of: the NSFW+ pack rows of a model, read from its stored transcript (deduped), since the
    board's row filter and tiers no longer carry pack rows (the old tree never sees them)."""
    from crucibleforge import config, report as R
    res = write_folder(tmp_path / "f", std_rows() + pack_rows() + pack_rows()[:1])
    monkeypatch.setattr(config, "RESULTS_DIR", res)      # restored after the test
    got = R._pack_rows_of(LABEL)
    assert [r["case_id"] for r in got] == [r["case_id"] for r in pack_rows()]
    assert all(NP.is_pack_row(r) for r in got)
