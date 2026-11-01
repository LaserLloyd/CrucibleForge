"""Suite 3.10 lane D (lock-in round, 2026-10-30), after D1:

- D3 (review H): the nightly re-judges unparsable verdicts (``judge
  --retry-failed``); a sub-score that still drops a row is marked
  "partial (n excluded)" on the board and in report.json, never MAXED.
- D4 (review B7): an easy leftover pack never gates the hard pack (same
  level), packs hold >= 3 questions unless ``allow_small`` with a reason, and
  a broken pack file never breaks the case set or bench-all.
- D6 (review I): the same-family judge warning reads the registry
  (``family:`` / ``arch:``) and model ids, never the label; 1-2-case parts
  get a ± floor footnote.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path


from crucibleforge import bench_all, ladder, report
from crucibleforge import config as cfgmod

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ D3

def test_nightly_judge_retries_failed_verdicts():
    """bench-all's judge phase carries retry_failed=True (the nightly calls
    bench-all; MAX_VERDICT_ATTEMPTS caps the retries)."""
    src = inspect.getsource(bench_all.execute)
    assert 'cmd="judge"' in src and "retry_failed=True" in src


def test_partial_excluded_part_is_never_maxed():
    assert report._maxed_unless_excluded(True, 0) is True
    assert report._maxed_unless_excluded(True, 2) is False
    assert report._excluded_tag(0) == ""
    assert report._excluded_tag(1) == " partial (1 excluded)"


def test_part_excluded_reads_failed_cases_and_block_counts():
    src = {"judge_failed_cases": ["NC1", "NC1", "WL2"],
           "nsfw": {"gated_parts": {"n_excluded": 3}}}
    assert report._part_excluded(src, {"NC1"}, {"rpdeep": 1}) == 2
    # lane A's block count wins when it says more
    assert report._part_excluded(src, {"NC1"}, {"nsfw_gated": 1}) == 3
    assert report._part_excluded(None, None, {}) == 0


def test_points_cell_says_partial_excluded():
    d = {"key": "nsfw", "label": "NSFW", "parts": [
        {"key": "nsfw_writing", "label": "NSFW writing", "short": "Writing", "weight": 4}]}
    hl = {"S": {"nsfw": 4.0}, "pts": {"nsfw/nsfw_writing": 4.0}, "m": {},
          "maxed": {"nsfw": {"parts": [], "category": False}},
          "excluded": {"nsfw/nsfw_writing": 1}}
    cell = report.points_cell(hl, d)
    assert "Writing 4.0 partial (1 excluded)" in cell and "MAXED" not in cell


def test_part_excluded_counts_failed_verdicts_of_the_part():
    rows = [{"case_id": "A", "judge": {"judge_failed": True}},
            {"case_id": "B", "judge": {"judge_failed": True, "empty_generation": True}},
            {"case_id": "C", "judge": {"judge_failed": False}},
            {"case_id": "D", "judge": {"judge_failed": True}}]
    assert report._n_excluded(rows, {"A", "B", "C"}) == 1
    assert report._n_excluded(rows, None) == 2


# ------------------------------------------------------------------ D4

def _spec(rel: str) -> dict:
    return json.loads((ROOT / "cases" / "ladder" / rel).read_text(encoding="utf-8"))


def test_leftover_packs_share_the_hard_packs_level():
    for part in ("nsfw/nsfw_writing", "chat/deep_rp"):
        s1, s2 = _spec(f"{part}/set-1.json"), _spec(f"{part}/set-2.json")
        assert s1["level"] == s2["level"], part
        # both open on the standard share alone: set-1 never gates set-2
        seq = [{"n": 1, "level": s1["level"]}, {"n": 2, "level": s2["level"]}]
        assert ladder.states(0.95, {1: 0.5, 2: None}, seq, 0.9) == {1: "scored", 2: "pending"}
        assert ladder.due(0.95, {}, seq, 0.9) == [1, 2]


def test_every_committed_pack_verifies():
    for f in sorted((ROOT / "cases" / "ladder").rglob("set-*.json")):
        problems, _ = ladder.verify_pack(f)
        assert not problems, (f, problems)


def test_pack_size_rule(tmp_path):
    d = tmp_path / "chat" / "deep_rp"
    d.mkdir(parents=True)
    f = d / "set-9.json"
    f.write_text(json.dumps({"level": 1, "cases": ["RD2-trieste", "RD3-granary"]}), encoding="utf-8")
    problems, _ = ladder.verify_pack(f)
    assert any("3 questions" in p for p in problems)
    f.write_text(json.dumps({"level": 1, "allow_small": True,
                             "cases": ["RD2-trieste", "RD3-granary"]}), encoding="utf-8")
    problems, _ = ladder.verify_pack(f)
    assert any("allow_small_reason" in p for p in problems)
    f.write_text(json.dumps({"level": 1, "allow_small": True, "allow_small_reason": "why",
                             "cases": ["RD2-trieste", "RD3-granary"]}), encoding="utf-8")
    problems, _ = ladder.verify_pack(f)
    assert not [p for p in problems if "question" in p or "allow_small" in p], problems


def test_a_broken_pack_never_breaks_the_case_set(tmp_path, monkeypatch, caplog):
    lad = tmp_path / "ladder"
    good = lad / "work" / "reasoning"
    good.mkdir(parents=True)
    (good / "set-1.json").write_text(json.dumps(
        {"level": 1, "cases": [{"id": "LZZ1-ok", "category": "reasoning", "prompt": "p",
                                "grader": "exact", "grader_config": {"answer": "1"}}]}),
        encoding="utf-8")
    (good / "set-2.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(ladder, "LADDER_DIR", lad)
    ids = [c["id"] for c in ladder.inline_cases()]
    assert ids == ["LZZ1-ok"]
    sets = ladder.discover({"scores": {"work": {"parts": {"reasoning": {
        "label": "R", "max": 3, "scoring": ["reasoning"]}}}}})
    assert [s["n"] for s in sets.get("work/reasoning", [])] == [1]
    # load_cases still works (the board and bench-all read it)
    assert any(c["id"] == "LZZ1-ok" for c in cfgmod.load_cases(["reasoning"]))
    # and `cases verify` reports the broken file
    probs = ladder.verify({"scores": {"work": {"parts": {"reasoning": {
        "label": "R", "max": 3, "scoring": ["reasoning"]}}}}})
    assert any("set-2.json" in p for p in probs)


# ------------------------------------------------------------------ D6

def test_family_from_registry_not_label():
    # an omega-arsenal-like fine-tune: nothing in its id or label says gemma
    entry = {"name": "omega-x", "model_id": "Org/Omega-X-31B-GGUF/Omega-X-31B-Q8_0",
             "family": "gemma"}
    assert report._model_family("omega-x", {}, entry) == "gemma"
    assert report._model_family("omega-x", {}, {**entry, "family": None, "arch": "gemma4"}) == "gemma"
    assert report._model_family("omega-x", {}, {"model_id": "Org/Omega-X"}) is None
    # the label alone never decides (a label is a free-form registry name)
    assert report._model_family("my-gemma-notes", {}, {"model_id": "Org/Other-7B"}) is None
    # meta.model_id first, then the registry id
    assert report._model_family("x", {"meta": {"model_id": "google/gemma-4-26b"}}, {}) == "gemma"


def test_family_overlap_note_uses_the_registry():
    by_name = {"omega-x": {"model_id": "Org/Omega-X-31B", "family": "gemma"}}
    stats = {"omega-x": {"meta": {"model_id": "Org/Omega-X-31B"},
                         "coverage": {"judges": ["Org/gemma-4-31B-judge-Q8"]}}}
    note = report._family_overlap_note(["omega-x"], stats, by_name)
    assert note and "omega-x (gemma" in note


def test_family_comes_from_the_rig_arch_at_registration(tmp_path):
    p = bench_all.plan({"providers": {"rig": {"type": "studioforge"}}, "models": []},
                       {"Org/Omega-X-31B"}, judge_id="j", case_ids=["A"],
                       rows_for=lambda l: [], pending_for=lambda l: 0, attempts_for=lambda l: {},
                       rungs=[], ladder_for=lambda l: None, rejudge_for=lambda l: set(),
                       rig_arch={"Org/Omega-X-31B": "gemma4"})
    assert p["add"][0]["arch"] == "gemma4"
    reg = tmp_path / "models.yaml"
    reg.write_text("models:\n- name: a\n  model_id: x/a\n", encoding="utf-8")
    bench_all.append_registry(reg, p["add"], "rig")
    assert "arch: gemma4" in reg.read_text(encoding="utf-8")


def test_floor_footnote_names_the_small_parts():
    defs = [{"key": "chat", "label": "Chat", "in_total": True, "parts": [
        {"key": "deep_rp", "label": "Deep RP", "cases": ["a", "b"]},
        {"key": "writing", "label": "Creative writing", "cases": ["w"]},
        {"key": "big", "label": "Big", "cases": ["1", "2", "3", "4"]}]}]
    txt = report.points_footnote(defs, {})
    assert "Deep RP" in txt and "Creative writing" in txt and "Big," not in txt
    assert "floor" in txt
