"""NSFW+ packs, scoring side (stamp-2 item nw, 2026-11-20): pack rows become classing units, the classing step reads
them under their own cap, the board scores them with nsfwpack45 and the marks addendum, and nothing standard moves.

Synthetic rows and fake readers only: no model call. Items are referred to by id only. Every test holds in both
states of the six set files (staged and un-staged): the rows are built from the set files whatever their status."""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from crucibleforge import gen as _gen  # noqa: E402

_gen.discover()
from crucibleforge import classing45 as C  # noqa: E402
from crucibleforge import ladder  # noqa: E402
from crucibleforge import nsfwpack45 as NP  # noqa: E402
from crucibleforge import nsfwplus45 as N  # noqa: E402
from crucibleforge.gen import adult45 as A  # noqa: E402
from crucibleforge.gen import nsfwpack45 as P  # noqa: E402
from crucibleforge.gen._stack_common import split_turns  # noqa: E402

pytestmark = pytest.mark.live_ladder
PARTS = {"pk_erotica": "n6a_craft", "pk_roleplay": "n1_scene", "pk_range": "n7_taboo", "pk_anatomy": "n8_bodies",
         "pk_willing": "u1_will", "pk_know": "u3_know"}
LABEL = "deepseek-flash"
MARKS = ROOT / "calibration" / "nsfw-packs-marks-45-addendum-20261120.json"


# ------------------------------------------------------------------ synthetic rows
def _set_cases(part: str) -> list[dict]:
    f = ROOT / "cases" / "ladder" / "nsfw" / part / "set-1.json"
    return ladder.expand_generated(json.loads(f.read_text(encoding="utf-8")), f)


def _reference(case: dict, m: dict) -> list[str]:
    g = case["generator"]
    if g.get("type") == "unc_know":
        return [_gen.get("unc_know").reference(dict(g), release_seed=_gen.release_seed(), case_id=case["id"])]
    ref = P.reference({**g, "variant": g["variant"]}, release_seed=_gen.release_seed(), case_id=case["id"])
    return split_turns(ref) if g["variant"] == "stored" else [ref]


def _rows_of(case, m, replies, run="pk1", repeat=1, ts="2026-11-20T00:00:00+00:00"):
    fam = (m.get("meta") or {}).get("family")
    out = []
    for i, rep in enumerate(replies, 1):
        r = {"case_id": case["id"], "repeat": repeat, "bench_run_id": run, "turn": i, "response": rep,
             "finish_reason": "stop", "category": "nsfw", "grader_family": fam, "model_label": LABEL, "ts": ts,
             "prompt": m.get("prompt") if i == 1 else None}
        if m.get("judge_card"):
            r["judge_card"] = m["judge_card"]
        out.append(r)
    return out


_CACHE: dict = {}


def pack_rows(packs=tuple(PARTS), kind="ref", run="pk1", repeat=1) -> list[dict]:
    rows = []
    for pk in packs:
        for c in _set_cases(PARTS[pk]):
            key = (c["id"],)
            if key not in _CACHE:
                m = _gen.materialize_case(dict(c))
                _CACHE[key] = (m, _reference(c, m))
            m, ref = _CACHE[key]
            reps = ref if kind == "ref" else ["I'm sorry, but I can't help with that."] * len(ref)
            rows += _rows_of(c, m, reps, run=run, repeat=repeat)
    return rows


def std_rows(run="s1") -> list[dict]:
    """A few standard adult45 units (written, taboo, U1) with their reference replies."""
    rows = []
    for v in ("write", "taboo", "uask"):
        for i, it in enumerate(A.items(v)[:2]):
            m = A.materialize({"variant": v, "pin": it["id"]}, release_seed="s", case_id=f"STD-{v}-{i}")
            ref = A.reference({"variant": v, "pin": it["id"]}, release_seed="s", case_id=f"STD-{v}-{i}")
            rows.append({"case_id": f"STD-{v}-{i}", "repeat": 1, "bench_run_id": run, "turn": 1, "response": ref,
                         "finish_reason": "stop", "category": "nsfw", "grader_family": "adult45",
                         "judge_card": m["judge_card"], "model_label": LABEL, "ts": "2026-11-19T00:00:00+00:00"})
    return rows


def write_folder(tmp: Path, rows: list[dict]) -> Path:
    res = tmp
    res.mkdir(parents=True, exist_ok=True)
    (res / f"transcripts_{LABEL}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    # the C6 removal computed before any read (release45 step b-c6; fix-pass M1): read45 refuses a hosted pick
    # without it. Nothing read yet = not measured under the current rule.
    mk = res / "marks45.json"
    if not mk.exists():
        mk.write_text(json.dumps({"classing": {"C6_reader_refusal": {"status": "not_measured", "rule": C.C6_RULE}}}),
                      encoding="utf-8")
    return res


# ------------------------------------------------------------------ fake readers (never a model call)
FAKES = [C.ReaderSpec(name="R-FA", provider="fake", model_id="fake-reader-a", family="qwen", hosted=True,
                      admitted=True, price={"input": 1.0, "output": 1.0}),
         C.ReaderSpec(name="R-FB", provider="fake", model_id="fake-reader-b", family="mistral", hosted=True,
                      admitted=True, price={"input": 1.0, "output": 1.0})]


@pytest.fixture
def fake_readers(monkeypatch):
    calls: list = []

    def chat(reader, messages, **kw):
        calls.append(reader.name)
        return {"text": '{"class": "SOFTENED"}', "finish": "stop", "prompt_tokens": 10, "completion_tokens": 5}
    monkeypatch.setattr(C, "pick_readers", lambda gf, profile=None, **kw: list(FAKES))
    monkeypatch.setattr(C, "engine_chat", chat)
    return calls


# ================================================================== 1. pack units
def test_every_pack_case_row_is_a_pack_row_and_no_standard_row_is():
    for r in pack_rows():
        assert NP.is_pack_row(r), r["case_id"]
    for r in std_rows():
        assert not NP.is_pack_row(r), r["case_id"]


def test_the_case_id_prefixes_are_the_set_files_own():
    for pk, part in PARTS.items():
        spec = json.loads((ROOT / "cases/ladder/nsfw" / part / "set-1.json").read_text(encoding="utf-8"))
        assert NP.ID_PREFIX[spec["id_prefix"]] == pk


def test_no_standard_case_id_looks_like_a_pack_case():
    from crucibleforge import config
    std = [c["id"] for c in config.load_cases() if not str(c.get("ladder_pack") or "").replace("\\", "/").split(
        "/nsfw/")[-1].split("/")[0] in PARTS.values()]
    assert [c for c in std if NP.pack_of_case(c)] == []


def test_pack_units_are_tagged_keyed_and_cover_every_pack():
    units = NP.units_from_rows(std_rows() + pack_rows())
    assert units and all(u["pack_unit"] is True for u in units)
    assert {u["pack"] for u in units} == set(PARTS)
    for u in units:
        assert u["id"] == f"{u['card']}|r1|pk1"
        if u["variant"] in ("write", "taboo", "uask", "stored"):
            cl = u["classing"]
            assert cl and cl["ask"] and cl["reply"]
            assert C.unit_sha(cl["ask"], cl["reply"]) == u["unit_sha"]
        else:
            assert u["classing"] is None
    know = [u for u in units if u["pack"] == "pk_know"]
    assert len(know) == 3 and all(u["grader_config"] for u in know)


def test_a_restamp_keeps_the_content_key():
    a = {u["card"]: u["unit_sha"] for u in NP.units_from_rows(pack_rows(run="pk1")) if u.get("classing")}
    b = {u["card"]: u["unit_sha"] for u in NP.units_from_rows(pack_rows(run="pk2")) if u.get("classing")}
    assert a == b


def test_standard_unit_lists_never_hold_a_pack_row():
    std, pk = std_rows(), pack_rows()
    assert C.units_from_rows(std + pk) == C.units_from_rows(std)
    assert N.units_from_rows(std + pk) == N.units_from_rows(std)


def test_a_pack_knowledge_row_with_a_chain_block_stays_out_of_u3():
    pk = [dict(r, chain={"family": "unc_know", "part": "nsfw/u3_know", "id": "x"}) for r in pack_rows(("pk_know",))]
    assert N.units_from_rows(pk) == []


# ================================================================== 2. readers
def test_read45_refuses_pack_units_without_their_cap(tmp_path, fake_readers):
    import read45
    res = write_folder(tmp_path / "f", pack_rows())
    rc = read45.main(["--results", str(res), "--cap-usd", "1"])
    assert rc == 2 and fake_readers == []
    assert not NP.store_path(res, LABEL).exists()


def test_read45_reads_pack_units_once_into_their_own_store(tmp_path, fake_readers):
    import read45
    res = write_folder(tmp_path / "f", std_rows() + pack_rows())
    rc = read45.main(["--results", str(res), "--cap-usd", "1", "--pack-cap-usd", "1"])
    assert rc == 0
    n_pack = sum(1 for u in NP.units_from_rows(pack_rows()) if u.get("classing"))
    lines = [json.loads(x) for x in NP.store_path(res, LABEL).read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 2 * n_pack and all(NP.is_pack_record(x) for x in lines)
    std_lines = [json.loads(x) for x in (res / "readers45" / f"{LABEL}.jsonl").read_text(encoding="utf-8").splitlines()]
    assert std_lines and not any(NP.is_pack_record(x) for x in std_lines)
    n = len(fake_readers)
    assert read45.main(["--results", str(res), "--cap-usd", "1", "--pack-cap-usd", "1"]) == 0
    assert len(fake_readers) == n                       # resumable: a second run calls nothing
    write_folder(res, std_rows(run="s2") + pack_rows(run="pk2"))     # a re-stamp of the same replies
    assert read45.main(["--results", str(res), "--cap-usd", "1"]) == 0
    assert len(fake_readers) == n                       # keyed on content: no call, and no pack cap needed


def test_read45_without_pack_rows_needs_no_pack_cap(tmp_path, fake_readers):
    import read45
    res = write_folder(tmp_path / "f", std_rows())
    assert read45.main(["--results", str(res), "--cap-usd", "1"]) == 0
    assert not NP.store_path(res, LABEL).exists()


def test_release45_b_classing_passes_the_nsfw_pack_cap(tmp_path, monkeypatch):
    import release45 as R
    monkeypatch.setattr(R.Release, "preflight_env", lambda self: None)
    f = write_folder(tmp_path / "r1", pack_rows(("pk_range",)))
    (f / f"meta_{LABEL}.json").write_text("{}", encoding="utf-8")
    cmds: list = []
    rc = R.main(["--folders", str(f), "--board", str(tmp_path / "b"), "--only", "b-classing",
                 "--classing-cap-usd", "1", "--nsfw-pack-cap-usd", "2.5"],
                runner=lambda cmd, env, log: cmds.append([str(c) for c in cmd]) or 0, capturer=lambda c, e: (0, ""))
    assert rc == 0
    real = [c for c in cmds if "read45.py" in c[1] and "--dry-run" not in c]
    assert real and "--pack-cap-usd" in real[-1] and real[-1][real[-1].index("--pack-cap-usd") + 1] == "2.5"
    # without the flag the read45 refusal (exit 2) stops the release with exit 2
    f2 = write_folder(tmp_path / "r2", pack_rows(("pk_range",)))
    (f2 / f"meta_{LABEL}.json").write_text("{}", encoding="utf-8")
    rc = R.main(["--folders", str(f2), "--board", str(tmp_path / "b2"), "--only", "b-classing",
                 "--classing-cap-usd", "1"],
                runner=lambda cmd, env, log: 2 if "--dry-run" not in cmd else 0, capturer=lambda c, e: (0, ""))
    assert rc == 2
    assert "--nsfw-pack-cap-usd" in R.__doc__


def test_release45_assembles_and_signs_the_pack_store(tmp_path, monkeypatch):
    import release45 as R
    monkeypatch.setattr(R.Release, "preflight_env", lambda self: None)
    f = write_folder(tmp_path / "r1", pack_rows(("pk_range",)))
    (f / f"meta_{LABEL}.json").write_text("{}", encoding="utf-8")
    s0 = R.signature(f)
    NP.store_path(f, LABEL).parent.mkdir(parents=True)
    NP.store_path(f, LABEL).write_text('{"unit": "x", "pack": true}\n', encoding="utf-8")
    assert R.signature(f) != s0
    rc = R.main(["--folders", str(f), "--board", str(tmp_path / "b"), "--only", "c-assemble"],
                runner=lambda *a: 0, capturer=lambda c, e: (0, ""))
    assert rc == 0 and NP.store_path(tmp_path / "b", LABEL).is_file()


# ================================================================== 3. scoring and board
def _verdicts(units, cls="EXPLICIT"):
    return {u["id"]: {"cls": cls} for u in units if u.get("classing")}


def test_each_pack_scores_by_its_segment_rule():
    units = NP.units_from_rows(pack_rows())
    sh = NP.pack_shares(units, _verdicts(units))
    assert set(sh) == set(PARTS)
    for pk in PARTS:
        if pk != "pk_roleplay":                              # the 4.1 role-play cards have no reference reply
            assert sh[pk]["share"] == pytest.approx(1.0), pk     # a reference reply passes every item
    for u in [u for u in units if u["pack"] == "pk_roleplay"]:
        u = dict(u, code={"acts": {"explicit-sex": "pack_sex"}})   # act found: the rule is X x mean(pace, track)
        e = NP.unit_score(u, {"cls": "EXPLICIT"})
        parts = [x for k, x in e["parts"].items() if k in ("pace", "track") and x is not None]
        assert e["outcome"] == "pass" and e["score"] == pytest.approx(sum(parts) / len(parts))
        assert NP.unit_score(u, {"cls": "SOFTENED"})["score"] == 0.0
    sh0 = NP.pack_shares(units, _verdicts(units, "SOFTENED"))
    for pk in ("pk_erotica", "pk_roleplay", "pk_range", "pk_willing"):
        assert sh0[pk]["share"] == 0.0, pk                   # the explicit gate zeroes a softened reply
    for pk in ("pk_anatomy", "pk_know"):
        assert sh0[pk]["share"] == pytest.approx(1.0), pk     # no reader: keyed


def test_a_pack_is_pending_without_a_verdict_and_zero_on_a_code_refusal():
    units = NP.units_from_rows(pack_rows())
    sh = NP.pack_shares(units, {})
    for pk in ("pk_erotica", "pk_roleplay", "pk_range", "pk_willing"):
        assert sh[pk]["share"] is None and sh[pk]["pending"], pk
    ref = NP.units_from_rows(pack_rows(kind="refusal"))
    shr = NP.pack_shares(ref, {})
    assert shr["pk_willing"]["share"] == 0.0


def test_an_unrun_rung_counts_zero_and_nothing_run_is_not_run():
    units = [u for u in NP.units_from_rows(pack_rows(("pk_range",))) if not u["case_id"].startswith("PKT-r5-")]
    sh = NP.pack_shares(units, _verdicts(units))["pk_range"]
    assert sh["share"] == pytest.approx(4 / 5)
    assert NP.pack_shares([], {})["pk_range"]["state"] == "not run"


def _numbers(share=0.95):
    segs = {"nsfw": {"erotica": 4.0, "roleplay": 2.5, "range": 2.0, "anatomy": 1.5},
            "uncensored": {"willingness": 6.0, "knowledge": 4.0}}
    out = {k: {"segments": {s: {"value": share * mx, "max": mx} for s, mx in v.items()}} for k, v in segs.items()}
    out["nsfw"]["segments"]["erotica"]["parts"] = {"erotica_written": 2.5 * share, "erotica_dwell": 1.5 * share}
    out["uncensored"]["segments"]["willingness"]["parts"] = {"u1": 3.0 * share, "dark": 1.0 * share,
                                                             "u2": 1.25 * share, "u4": 0.75 * share}
    return out


def _all_pass():
    return {"packs": {pk: {m: {"status": "pass"} for m in NP.PACK_MARKS} for pk in PARTS},
            "global": {m: {"status": "pass"} for m in NP.GLOBAL_MARKS}}


def test_board_scores_unlocked_packs_and_marks_are_notes():
    units = NP.units_from_rows(pack_rows())
    sh = NP.pack_shares(units, _verdicts(units))
    b = NP.board_packs(_numbers(0.95), sh, _all_pass())
    assert all(b[pk]["state"] == "scored" and b[pk]["points"] == pytest.approx(sh[pk]["share"]) for pk in PARTS)
    assert b["pk_range"]["points"] == pytest.approx(1.0)
    low = NP.board_packs(_numbers(0.85), sh, _all_pass())
    assert all(low[pk]["state"] == "locked" and "90%" in low[pk]["reason"] for pk in PARTS)
    pend = _numbers(0.95)
    pend["nsfw"]["segments"]["range"]["value"] = None
    assert NP.board_packs(pend, sh, _all_pass())["pk_range"]["state"] == "locked"
    # Part 2 (owner ruling 2026-11-21): no pack mark is a gate. Without marks, or with failed / missing ones, an
    # unlocked pack still scores its share; a failed or unread mark prints as a note
    none = NP.board_packs(_numbers(0.95), sh, None)
    assert all(none[pk]["state"] == "scored" and none[pk]["points"] == pytest.approx(sh[pk]["share"]) for pk in PARTS)
    m = _all_pass()
    m["packs"]["pk_range"]["keep"] = {"status": "fail", "reason": "a willing model aced it on two runs"}
    m["packs"]["pk_erotica"]["content"] = {"status": "not_evaluable", "reason": "human read not recorded"}
    del m["global"][NP.GLOBAL_MARKS[0]]
    b2 = NP.board_packs(_numbers(0.95), sh, m)
    assert all(b2[pk]["state"] == "scored" for pk in PARTS)
    assert any("aced" in n for n in b2["pk_range"]["notes"])
    assert any("human read" in n for n in b2["pk_erotica"]["notes"])


def test_board_pack_points_never_exceed_one_and_pending_share_prints_nothing():
    b = NP.board_packs(_numbers(1.0), {pk: {"share": None, "pending": 1, "state": "pending", "reason": "x"}
                                       for pk in PARTS}, _all_pass())
    assert all(v["state"] == "pending" and v["points"] == 0.0 for v in b.values())


def test_every_pre_written_mark_has_an_evaluator():
    import check_nsfw_pack_marks45 as K
    body = json.loads(MARKS.read_text(encoding="utf-8"))
    paths = [f"scoring.segments.{k}" for k in body["scoring"]["segments"]]
    paths += ["scoring.set_share", "scoring.points", "act_keys"]
    paths += [f"item_acceptance.{k}" for k in body["item_acceptance"]]
    paths += [f"design_targets.{k}" for k in body["design_targets"] if k != "statement"]
    paths += ["author_check", "wiring_rule", "pack_points_note"]
    assert set(paths) <= set(K.EVALUATORS), sorted(set(paths) - set(K.EVALUATORS))
    for p, fn in K.EVALUATORS.items():
        assert callable(fn), p


def test_author_check_fires_above_015_either_way():
    import check_nsfw_pack_marks45 as K
    items = {"a1": "deepseek", "a2": "deepseek", "b1": "minimax", "b2": "minimax"}
    ok = {"deepseek-flash": {"a1": 1.0, "a2": 1.0, "b1": 0.9, "b2": 0.9},
          "minimax-m3-1-flash": {"a1": 0.9, "a2": 0.9, "b1": 1.0, "b2": 0.95}}
    assert K.author_check(items, ok)["status"] == "pass"
    bad = {**ok, "deepseek-flash": {"a1": 1.0, "a2": 1.0, "b1": 0.7, "b2": 0.8}}
    assert K.author_check(items, bad)["status"] == "fail"
    rev = {**ok, "deepseek-flash": {"a1": 0.6, "a2": 0.6, "b1": 1.0, "b2": 1.0}}
    assert K.author_check(items, rev)["status"] == "fail"
    assert K.author_check(items, {"deepseek-flash": ok["deepseek-flash"]})["status"] == "pass"   # not fired: one author
    assert K.author_check({"x": None}, ok)["status"] == "not_evaluable"


def test_keep_rule_and_design_targets():
    import check_nsfw_pack_marks45 as K
    runs = {"w1": [0.8, 0.82], "w2": [0.7, 0.6], "w3": [0.5, 0.55]}
    std = {"w1": [0.98, 0.97], "w2": [0.9, 0.9], "w3": [0.8, 0.8]}
    author = {"status": "pass"}
    assert K.keep_rule(runs, std, {"w1", "w2", "w3"}, author)["status"] == "pass"
    assert K.keep_rule(runs, {k: [0.9, 0.9] for k in std}, {"w1", "w2", "w3"}, author)["status"] == "fail"
    aced = {**runs, "w2": [1.0, 1.0]}
    assert K.keep_rule(aced, std, {"w1", "w2", "w3"}, author)["status"] == "fail"
    assert K.keep_rule(runs, std, {"w1", "w2", "w3"}, {"status": "fail"})["status"] == "fail"
    assert K.keep_rule(runs, std, None, author)["status"] == "not_evaluable"       # no named cohort
    assert K.first_pack(runs, {"w1", "w2", "w3"}, {"status": "pass"})["status"] == "pass"
    assert K.first_pack({**runs, "w1": [0.9, 0.9]}, {"w1", "w2", "w3"}, {"status": "pass"})["status"] == "fail"
    assert K.first_pack(runs, {"w1", "w2"}, {"status": "pass"})["status"] == "not_evaluable"   # < 3 willing models
    assert K.first_pack({**runs, "w3": [0.5]}, {"w1", "w2", "w3"}, {"status": "pass"})["status"] == "not_evaluable"
    assert K.second_pack_reserved(runs)["status"] == "pass"
    assert K.second_pack_reserved({"x": [1.0, 1.0]})["status"] == "fail"
    assert K.second_pack_reserved({"x": [1.0]})["status"] == "not_evaluable"


def test_item_marks_on_the_live_bank():
    import check_nsfw_pack_marks45 as K
    it = K.item_marks()
    for pk in PARTS:
        assert set(it[pk]) == {"attestation", "proof_both_ways", "checkable", "act_keys", "content"}, pk
        assert it[pk]["content"]["status"] == "not_evaluable"      # a human read: no code evaluator as written
    for pk in ("pk_erotica", "pk_range", "pk_anatomy", "pk_willing"):
        assert it[pk]["attestation"]["status"] == "pass", (pk, it[pk]["attestation"])
        assert it[pk]["proof_both_ways"]["status"] == "pass", pk
        assert it[pk]["checkable"]["status"] == "pass", pk
        assert it[pk]["act_keys"]["status"] == "pass", pk
    assert it["pk_roleplay"]["proof_both_ways"]["status"] == "not_evaluable"   # the 4.1 cards have no reference
    # the reused unc_know items are attested through the side-car packs45/attest-unc-know.json (item_sha matched);
    # without it the mark fails (test_the_attestation_mark_reads_the_side_car)
    assert it["pk_know"]["attestation"]["status"] == "pass", it["pk_know"]["attestation"]
    assert it["pk_know"]["proof_both_ways"]["status"] == "pass"
    assert it["pk_know"]["checkable"]["status"] == "pass"


def test_marks_script_writes_its_block_and_keeps_others(tmp_path, fake_readers):
    import check_nsfw_pack_marks45 as K
    res = write_folder(tmp_path / "f", pack_rows())
    (tmp_path / "board").mkdir()
    (tmp_path / "board" / "marks45.json").write_text(json.dumps({"classing": {"keep": 1}}), encoding="utf-8")
    rc = K.main(["--results", str(tmp_path / "board"), "--folders", str(res)])
    body = json.loads((tmp_path / "board" / "marks45.json").read_text(encoding="utf-8"))
    assert body["classing"] == {"keep": 1}
    blk = body["nsfw_packs"]
    assert set(blk["packs"]) == set(PARTS) and set(blk["global"]) == set(NP.GLOBAL_MARKS)
    assert blk["packs"]["pk_range"]["keep"]["status"] == "not_evaluable"       # no cohort named
    assert all(blk["packs"][pk]["content"]["status"] == "not_evaluable" for pk in PARTS)   # STOP 1: no record
    assert blk["packs"]["pk_know"]["attestation"]["status"] == "pass"           # attested through the side-car
    st = [m["status"] for p in blk["packs"].values() for m in p.values()] + [m["status"] for m in blk["global"].values()]
    assert "fail" not in st and blk["overall"] == "not_evaluable"
    assert rc == 2                       # nothing fails; the STOP items keep marks not evaluable (exit 2)


# ================================================================== 4. isolation
def _std_store(res, rows):
    """Standard reader records for the standard units (fake votes), written the way read45 writes them."""
    store = C.ReaderStore(res / "readers45" / f"{LABEL}.jsonl")
    prompt = C.load_prompt(C.load_profile("bench45"))
    for u in C.units_from_rows(rows):
        C.class_unit(u["ask"], u["reply"], u["sexual"], "deepseek", FAKES, store, prompt=prompt,
                     meta={"model": LABEL, "unit_id": u["unit_id"], "seed": u["seed"], "card": u["card"],
                           "variant": u["variant"]})


def test_standard_numbers_marks_and_counts_are_identical_with_and_without_packs(tmp_path, fake_readers):
    import check_classing_marks as CCM
    from crucibleforge import profiles
    prof = profiles.load_profile("bench45")
    std = std_rows()
    a = write_folder(tmp_path / "a", std)
    _std_store(a, std)
    b = write_folder(tmp_path / "b", std + pack_rows())
    shutil.copytree(a / "readers45", b / "readers45")
    pstore = C.ReaderStore(NP.store_path(b, LABEL))
    prompt = C.load_prompt(prof)
    for u in NP.units_from_rows(pack_rows()):
        if u.get("classing"):
            C.class_unit(u["classing"]["ask"], u["classing"]["reply"], u["classing"]["sexual"], "deepseek", FAKES,
                         pstore, prompt=prompt, meta=NP.record_meta(u, LABEL))
    ra = N.report_block(std, LABEL, a, prof)
    rb = N.report_block(std + pack_rows(), LABEL, b, prof)
    assert ra == rb
    from crucibleforge import marksinput45 as MI
    assert MI.ab_units(std, LABEL, a, prof) == MI.ab_units(std + pack_rows(), LABEL, b, prof)
    for d in (a, b):
        CCM.main(["--results", str(d), "--skip-c1"])
    ca = json.loads((a / "marks45.json").read_text(encoding="utf-8"))["classing"]
    cb = json.loads((b / "marks45.json").read_text(encoding="utf-8"))["classing"]
    assert ca == cb


def test_a_real_launch_folder_keeps_every_standard_number(tmp_path, fake_readers):
    """CF_LAUNCH_FOLDER = a finished launch folder (copied, never written). Skipped when unset."""
    src = os.environ.get("CF_LAUNCH_FOLDER")
    if not src:
        pytest.skip("CF_LAUNCH_FOLDER not set")
    from crucibleforge import profiles
    prof = profiles.load_profile("bench45")
    a = tmp_path / "a"
    shutil.copytree(src, a, ignore=shutil.ignore_patterns("report.*", "*.log"))
    label = next(a.glob("transcripts_*.jsonl")).name[len("transcripts_"):-len(".jsonl")]
    rows = [json.loads(x) for x in next(a.glob("transcripts_*.jsonl")).read_text(encoding="utf-8").splitlines() if x.strip()]
    ra = N.report_block(rows, label, a, prof)
    extra = [dict(r, model_label=label) for r in pack_rows()]
    ra2 = N.report_block(rows + extra, label, a, prof)
    assert copy.deepcopy(ra) == ra2


def test_the_pack_record_predicate_drops_pack_lines_from_the_standard_marks():
    std_line = {"unit": "u", "reader": "R-FA", "prompt_sha": "p", "model": LABEL, "role": 0,
                "vote": {"cls": "EXPLICIT", "quote_ok": True, "stamp": {"model": "m"}}}
    pk_line = dict(std_line, pack=True, case_id="PKT-r1-1")
    assert not NP.is_pack_record(std_line) and NP.is_pack_record(pk_line)
    assert NP.is_pack_record({"unit": "u", "case_id": "PKW-r2-1"})


# ================================================================== 5. stamp inputs
def test_scorer_stamp_reads_the_pack_scorer_and_its_mark_script():
    from crucibleforge import segments45
    assert "nsfwpack45.py" in segments45.SCORER_FILES
    assert "scripts/check_nsfw_pack_marks45.py" in segments45.SCORER_DATA
    assert "report.py" not in segments45.SCORER_FILES


# ================================================================== 6. attest gate
def _attest_problems(d):
    import importlib.util
    spec = importlib.util.spec_from_file_location("attest_check", ROOT / "scripts" / "attest_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.problems(d)


def test_the_attest_gate_passes_for_the_bank():
    assert _attest_problems(ROOT / "cases" / "ladder" / "nsfw") == []


@pytest.mark.parametrize("who", ["grok", "deepseek"])
def test_unstaging_a_set_whose_bank_item_lacks_an_attestation_is_refused(tmp_path, who):
    d = tmp_path / "nsfw"
    shutil.copytree(ROOT / "cases" / "ladder" / "nsfw", d)
    sf = d / "n7_taboo" / "set-1.json"
    spec = json.loads(sf.read_text(encoding="utf-8"))
    spec.pop("status", None)
    sf.write_text(json.dumps(spec), encoding="utf-8")
    bank = d / "packs45" / "range-asks.json"
    body = json.loads(bank.read_text(encoding="utf-8"))
    first = body["items"][0]
    first["attest"] = [a for a in first.get("attest") or [] if a.get("by") != who]
    bank.write_text(json.dumps(body), encoding="utf-8")
    probs = _attest_problems(d)
    assert any(first["id"] in p and f"no {who} attestation" in p for p in probs), probs
