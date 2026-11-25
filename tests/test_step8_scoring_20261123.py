"""Step 8 (scoring and report half; review A F1-F8, review B finding 2): replicate ranges on the board's own scale,
the mean of runs on the board, NSFW+ /20 beside Overall, the assembled marks, the release default order."""
from __future__ import annotations

import inspect
import json
import math
import sys
from pathlib import Path

import pytest

from crucibleforge import accuracy45 as A
from crucibleforge import profiles
from crucibleforge import report as R
from crucibleforge import resolution45 as RS
import importlib
RH = importlib.import_module("crucibleforge.templates.board.render_html")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release45 as REL  # noqa: E402
from test_release45 import Sim, mkfolder  # noqa: E402

PROF = profiles.load_profile("bench45")
ST = A.settings(PROF)


@pytest.fixture(autouse=True)
def _no_env_preflight(monkeypatch):
    monkeypatch.setattr(REL.Release, "preflight_env", lambda self: None)


# ------------------------------------------------------------------ helpers
def _measured(code, chat, nsfw=None, unc=None, code_packs=0.5, writes=3.0, pending_code=0):
    m = {"code": {"points": code, "pending_points": pending_code, "pack_points": code_packs,
                  "segments": {"writes": {"points": writes, "pending_points": 0},
                               "fixes": {"points": None, "pending_points": 3.0}}},
         "chat": {"points": chat, "pending_points": 0, "pack_points": 1.0,
                  "segments": {"character": {"points": 3.0, "pending_points": 0}}},
         "overall": {"points": None if pending_code else code + chat}}
    if nsfw is not None:
        m["nsfw_plus"] = {"nsfw": {"points": nsfw, "pending": None,
                                   "segments": {"erotica": {"points": nsfw / 2, "pending": None}}},
                          "uncensored": {"points": unc, "pending": None, "segments": {}}}
    return {"measured": m}


def _folder(tmp: Path, name: str, label: str, model: dict) -> Path:
    f = tmp / name
    f.mkdir()
    (f / f"transcripts_{label}.jsonl").write_text("{}\n", encoding="utf-8")
    (f / "report.json").write_text(json.dumps({"models": {label: model}}), encoding="utf-8")
    return f


# ------------------------------------------------------------------ item 1: the spread on the board's scale
def test_folder_points_read_the_rendered_report_on_the_board_scale(tmp_path):
    f = _folder(tmp_path, "r1", "ref", _measured(8.6, 8.2, nsfw=4.0, unc=7.0))
    label, p = RS.folder_points(f)
    assert label == "ref"
    assert p["code"] == 8.6 and p["chat"] == 8.2 and p["overall"] == pytest.approx(16.8)
    assert p["nsfw"] == 4.0 and p["uncensored"] == 7.0 and p["nsfw_plus"] == 11.0
    assert p["code/writes"] == 3.0 and "code/fixes" not in p            # a pending segment is no run of it
    assert p["nsfw/erotica"] == 2.0 and p["code_packs"] == 0.5
    # old-tree keys are gone from the release path
    assert not {"work", "nsfw/a45", "chat/h2_state"} & set(p)


def test_runs_are_counted_per_key_and_the_file_says_its_scale(tmp_path):
    pts = {"ref": [RS.folder_points(_folder(tmp_path, "a", "ref", _measured(8.6, 8.2, 4.0, 7.0)))[1],
                   RS.folder_points(_folder(tmp_path, "b", "ref", _measured(8.8, 8.0)))[1]],  # no adult rows
           "m": [RS.folder_points(_folder(tmp_path, "c", "m", _measured(7.0, 6.0, 3.0, 5.0)))[1],
                 RS.folder_points(_folder(tmp_path, "d", "m", _measured(7.2, 6.4, 3.4, 5.4)))[1]]}
    body = RS.precision_file(pts, ["a", "b", "c", "d"])
    r = body["run_to_run"]
    assert body["scale"] == "board45"
    assert r["n"]["ref"]["code"] == 2 and r["n"]["ref"]["nsfw"] == 1      # an adult-less folder is no adult run
    assert r["by_model"]["ref"]["nsfw"] is None
    assert r["pooled_df"]["code"] == 2 and r["pooled_df"]["nsfw"] == 1
    assert r["pooled_by"]["nsfw"] == ["m"]
    assert r["by_model"]["m"]["code"] == pytest.approx(math.sqrt(0.02))
    assert "pooled SD" in RS.render_precision(r)


def test_the_release_resolution_step_reads_reports_only():
    src = inspect.getsource(RS)
    assert "boot_model" not in src and "H.draws" not in src and "folder_samples" not in src
    script = (ROOT / "scripts" / "resolution45.py").read_text(encoding="utf-8")
    assert "RS.resolve" not in script and "reference_share" not in script


# ------------------------------------------------------------------ item 2: the printed range
def _rtr():
    return RS.run_to_run({"ref": [{"code": 8.66, "nsfw_plus": 10.54, "code/writes": 3.6},
                                  {"code": 8.55, "nsfw_plus": 12.97, "code/writes": 3.5},
                                  {"code": 8.74, "nsfw_plus": 12.26, "code/writes": 3.7}],
                          "ds": [{"code": 9.41, "nsfw_plus": 13.3}, {"code": 9.58, "nsfw_plus": 13.2}],
                          "one": [{"code": 5.1, "nsfw_plus": 13.4}]})


def _model():
    return {"segments45": {"code": {"segments": {"writes": {"max": 4.0, "units": [
        {"id": "c1_implied", "weight": 4.0, "share": 0.9}]}}}},
            "repeat45": {"work/c1_implied": 0.0}, "repeat45_n": {"work/c1_implied": 2},
            "repeat45_n1": {"work/c1_implied": 1},
            "nsfw_plus": {"numbers": {"nsfw": {"segments": {"erotica": {"max": 4.0}}},
                                      "uncensored": {"segments": {"knowledge": {"max": 4.0}}}}}}


def test_a_few_replicates_give_the_mean_range_from_the_pooled_spread():
    r = _rtr()
    acc = A.printed_accuracy(_model(), PROF, label="ref", runs=r, estimates={})
    df = r["pooled_df"]["code"]
    assert acc["code"]["half"] == pytest.approx(A.t90(df) * r["pooled"]["code"] / math.sqrt(3), abs=1e-4)
    assert acc["code"]["source"] == "pooled" and acc["code"]["n"] == 3
    assert "mean of 3 runs" in A.range_text(acc["code"])
    assert acc["segments"]["code/writes"]["source"] == "pooled"


def test_five_or_more_replicates_use_the_models_own_spread():
    r = _rtr()
    r = {**r, "n": {**r["n"], "ref": {**r["n"]["ref"], "code": 5}}}
    acc = A.printed_accuracy(_model(), PROF, label="ref", runs=r, estimates={})
    sd = r["by_model"]["ref"]["code"]
    assert acc["code"]["half"] == pytest.approx(A.t90(4) * sd / math.sqrt(5), abs=1e-4)
    assert acc["code"]["source"] == "replicates" and "own spread (4 df)" in A.range_text(acc["code"])


def test_t90_is_wider_for_few_runs_and_the_normal_value_for_many():
    assert A.t90(1) == 6.314 and A.t90(4) == 2.132 and A.t90(31) == A.Z90 and A.t90(None) == A.Z90
    assert all(A.t90(d) > A.t90(d + 1) for d in range(1, 30))


def test_a_single_run_model_gets_the_pooled_spread_labelled():
    r = _rtr()
    acc = A.printed_accuracy(_model(), PROF, label="one", runs=r, estimates={})
    assert acc["code"]["half"] == pytest.approx(A.t90(r["pooled_df"]["code"]) * r["pooled"]["code"], abs=1e-4)
    t = A.range_text(acc["code"])
    assert acc["code"]["source"] == "pooled" and "pooled from 2 other model(s)" in t and "3 df" in t


def test_nsfw_plus_gets_a_range_from_the_runs():                            # review A F2
    acc = A.printed_accuracy(_model(), PROF, label="ref", runs=_rtr(), estimates={})
    assert acc["nsfw_plus"]["half"] is not None and acc["nsfw_plus"]["source"] == "pooled"
    before = A.model_accuracy(_model(), PROF, estimates={})                 # the stamped rule: never measured
    assert before["nsfw_plus"]["half"] is None


def test_a_measured_figure_is_never_replaced_by_a_larger_stored_one():     # review A F1
    m = {"repeat45": {"work/c5_tools": 0.02, "work/c5_toolgen": None},
         "repeat45_n": {"work/c5_tools": 10, "work/c5_toolgen": 1},
         "repeat45_n1": {"work/c5_tools": 10, "work/c5_toolgen": 1}}
    g = {"max": 2.0, "units": [{"id": "c5_tools", "weight": 1.0, "share": 0.8},
                               {"id": "c5_toolgen", "weight": 1.0, "share": 0.7}]}
    est = {"code/tools": 14.3}
    old = A._segment(m, "code", "tools", g, {}, est, ST)
    new = A._segment(m, "code", "tools", g, {}, est, ST, printed=True)
    assert old["half"] == pytest.approx(0.286)                             # stored 14.3% of 2 wins today
    assert new["half"] == pytest.approx(A.Z90 * 0.02) and new["source"] == "repeats"
    assert "not in this figure" in new["note"]


def test_writes_zero_spread_from_an_unreached_redraw_is_not_measured():   # review A F8
    m = _model()
    old = A.model_accuracy(m, PROF, estimates={"code/writes": 2.2})["segments"]["code/writes"]
    assert old["half"] == 0.0                                              # the "±0.00" the board printed
    new = A.model_accuracy(m, PROF, estimates={"code/writes": 2.2}, printed=True)["segments"]["code/writes"]
    assert new["source"] == "estimate" and new["half"] == pytest.approx(0.088)
    assert "single repeat" in new["note"]
    nm = A.model_accuracy(m, PROF, estimates={}, printed=True)["segments"]["code/writes"]
    assert nm["half"] is None and A.range_text(nm).startswith("range not measured")


def test_repeat_noise_counts_only_the_repeats_the_redraw_reaches():
    src = inspect.getsource(R.repeat_noise45)
    assert '"n1"' in src and "tier == 1" in src


# ------------------------------------------------------------------ item 3: the mean of runs on the board
def test_old_tree_replicates_never_reach_the_board(tmp_path, monkeypatch):
    (tmp_path / "precision45.json").write_text(json.dumps({"run_to_run": {"runs": {"x": 2}}}), encoding="utf-8")
    monkeypatch.setattr(R, "results_dir", lambda: tmp_path)
    R._RUNS_CACHE.clear()
    assert R.run_to_run_result() is None                                  # an old-tree file is ignored
    (tmp_path / "precision45.json").write_text(json.dumps({"scale": "board45", "run_to_run": {"runs": {"x": 2}}}),
                                              encoding="utf-8")
    R._RUNS_CACHE.clear()
    assert R.run_to_run_result() == {"runs": {"x": 2}}
    # neither the item bootstrap nor the headline summary reads the run file any more (F3, F5)
    assert "run_to_run_result" not in inspect.getsource(R.model_boots)
    assert "replicate=" not in inspect.getsource(R._headline)


def _stats():
    tb = {"code": {"points": 8.74, "pack_points": 2.2, "segments": {"writes": {"points": 3.6}}},
          "chat": {"points": 8.10, "pack_points": 1.5, "segments": {}},
          "overall": {"points": 16.84, "pending": [], "pack_points": 3.7}}
    nums = {"nsfw": {"value": 4.5, "partial": 4.5, "segments": {"erotica": {"value": 1.9}}},
            "uncensored": {"value": 7.76, "partial": 7.76, "segments": {}}}
    return {"segments45": tb, "headline": {"S": {}, "P": {}, "T1": {}, "pack_of": {"work": 5.0, "chat": 2.0}},
            "nsfw_plus": {"numbers": nums, "total": {"value": 12.26, "partial": 12.26}}}


def test_a_replicated_model_is_shown_at_the_mean_of_its_runs():
    pts = {"ref": [{"code": 8.66, "chat": 8.29, "overall": 16.96, "code_packs": 1.25, "chat_packs": 1.56,
                    "nsfw": 3.64, "uncensored": 6.90, "nsfw_plus": 10.54, "code/writes": 3.60},
                   {"code": 8.55, "chat": 8.02, "overall": 16.58, "code_packs": 2.0, "chat_packs": 1.0,
                    "nsfw": 5.55, "uncensored": 7.42, "nsfw_plus": 12.97, "code/writes": 3.50},
                   {"code": 8.74, "chat": 8.10, "overall": 16.84, "code_packs": 2.2, "chat_packs": 1.5,
                    "nsfw": 4.50, "uncensored": 7.76, "nsfw_plus": 12.26, "code/writes": 3.60}]}
    r = RS.run_to_run(pts)
    s = _stats()
    R._replicate_means45("ref", s, r)
    tb, hl = s["segments45"], s["headline"]
    assert tb["code"]["points"] == pytest.approx(8.65, abs=1e-3)
    assert tb["code"]["pack_points"] == pytest.approx((1.25 + 2.0 + 2.2) / 3, abs=1e-3)   # mean of each run's packs
    assert hl["T1"]["work"] == pytest.approx(8.65, abs=1e-3)
    assert hl["P"]["work"] == pytest.approx(8.65 + 5.45 / 3, abs=1e-3)
    assert tb["overall"]["points"] == pytest.approx(16.793, abs=1e-3) and hl["sort"] == pytest.approx(16.793, abs=1e-3)
    assert s["nsfw_plus"]["total"]["value"] == pytest.approx(11.923, abs=1e-3)
    assert s["nsfw_plus"]["numbers"]["nsfw"]["value"] == pytest.approx(4.563, abs=1e-3)
    note = R.replicate_note(hl)
    assert note.startswith("3 runs (scores are the mean of the runs)")
    assert "Overall 16.6–17.0" in note and "NSFW+ 10.5–13.0" in note


def test_a_single_run_keeps_its_own_numbers_and_says_so():
    r = RS.run_to_run({"one": [{"code": 5.1, "overall": 10.6}]})
    s = _stats()
    R._replicate_means45("one", s, r)
    assert s["segments45"]["code"]["points"] == 8.74 and R.replicate_note(s["headline"]) == "single run"
    s2 = _stats()
    R._replicate_means45("one", s2, None)                                  # a folder report: no file, no note
    assert "replicates" not in s2["headline"]


# ------------------------------------------------------------------ item 4: the board carries the folders' marks
def _go(tmp, monkeypatch, argv, sim=None):
    sim = sim or Sim(monkeypatch)
    folders = sorted(p for p in tmp.iterdir() if p.is_dir() and not p.name.startswith("board"))
    rc = REL.main(["--folders", *map(str, folders), "--board", str(tmp / "board45"), *argv], runner=sim.runner,
                  capturer=sim.capturer)
    return rc, sim


def test_assemble_copies_the_folders_mark_results(tmp_path, monkeypatch):
    for n, lab in (("r1", "a"), ("r2", "b")):
        f = mkfolder(tmp_path, n, lab)
        (f / "marks45.json").write_text(json.dumps({"act_beat": {"state": "failed", "why": "fade backstop"},
                                                    "segments": {"v": n}}), encoding="utf-8")
    rc, _ = _go(tmp_path, monkeypatch, ["--only", "c-assemble"])
    assert rc == 0
    body = json.loads((tmp_path / "board45" / "marks45.json").read_text(encoding="utf-8"))
    assert body["act_beat"]["state"] == "failed"                           # failed stays failed on the board
    assert "segments" not in body                                          # folders disagree: not copied
    assert "disagree on block 'segments'" in (tmp_path / "board45" / "release45.log").read_text(encoding="utf-8")


# ------------------------------------------------------------------ item 5: NSFW+ /20 beside Overall
def test_status_line_no_longer_says_never_added_together():
    st = json.dumps(PROF.get("board_notes") or {})
    assert "never added together" not in st and "NSFW+ /20 = NSFW /10 + Uncensored /10" in st


def test_nsfw_plus_column_sits_beside_overall():
    main, _ = RH.columns([], scores=[{"key": "work", "label": "Code /10"}], overall_label="Overall /20",
                         beside_overall=[{"key": "adult_nsfw_plus", "label": "NSFW+ /20"}])
    keys = [c["key"] for c in main]
    assert keys.index("adult_nsfw_plus") == keys.index("overall") + 1
    main0, _ = RH.columns([], scores=[{"key": "work", "label": "Code"}])
    assert "adult_nsfw_plus" not in [c["key"] for c in main0]               # the 4.1 page is unchanged
    src = inspect.getsource(R.render_scorecard) if hasattr(R, "render_scorecard") else \
        (ROOT / "crucibleforge" / "report.py").read_text(encoding="utf-8")
    # R37/R38 (2026-11-26): the head carries the maximum with packs from the data, never a fixed /20
    assert '([head45("NSFW+", "nsfw_plus", stats, 20)] if np_col else [])' in src


def test_nsfw_plus_total_cell_prints_its_range_and_source():
    s = {"nsfw_plus": {"numbers": {"nsfw": {"value": 4.5}, "uncensored": {"value": 7.8}},
                       "total": {"value": 12.3},
                       "accuracy": {"nsfw_plus": A.block(0.66, 20.0, "replicates", ST,
                                                         basis="mean of 3 runs, own spread", n=3)}}}
    c = R.np_total_cell(s)
    assert c.startswith("12.3 ±0.66") and "mean of 3 runs" in c
    s["nsfw_plus"]["accuracy"]["nsfw_plus"] = A.block(None, 20.0, None, ST, "NSFW+ not run")
    assert "range not measured (NSFW+ not run)" in R.np_total_cell(s)


# ------------------------------------------------------------------ item 7: the release default order and flags
def test_default_release_measures_the_spread_before_the_board():
    d = REL.DEFAULT_STEPS
    assert d == ("b-judge", "c-assemble", "d-report", "e-resolution", "e-board", "e-accuracy")
    assert REL.STEPS.index("d-report") < REL.STEPS.index("e-resolution") < REL.STEPS.index("e-board") \
        < REL.STEPS.index("e-accuracy")


def test_folder_reports_run_in_parallel_and_force_reruns(tmp_path, monkeypatch, capsys):
    for i in range(4):
        mkfolder(tmp_path, f"r{i}", f"m{i}")
    rc, sim = _go(tmp_path, monkeypatch, ["--only", "c-assemble,d-report,e-board", "--report-jobs", "3"])
    assert rc == 0
    reports = [c for c in sim.cmds if "report" in c]
    assert len(reports) == 5                                                # four folders + the board
    rc, sim2 = _go(tmp_path, monkeypatch, ["--only", "c-assemble,d-report,e-board"], sim=Sim(monkeypatch))
    assert rc == 0 and sim2.cmds == []                                     # done at this commit
    rc, sim3 = _go(tmp_path, monkeypatch, ["--only", "c-assemble,d-report,e-board", "--force", "e-board"],
                   sim=Sim(monkeypatch))
    assert rc == 0 and len(sim3.cmds) == 1 and str(tmp_path / "board45") in sim3.cmds[0]
    rc, _ = _go(tmp_path, monkeypatch, ["--force", "no-such-step"], sim=Sim(monkeypatch))
    assert rc == 2


def test_the_release_names_no_person():
    src = (ROOT / "scripts" / "release45.py").read_text(encoding="utf-8")
    assert ("Ja" + "ke") not in src and "the owner's call" in src
