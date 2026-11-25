"""Fix pass B: what the realigned board prints (S11 Overall without packs, M9 public data, notes, marks state)."""
import json
import re
from pathlib import Path

import pytest
import yaml

from crucibleforge import report, segments45 as S

ROOT = Path(__file__).resolve().parent.parent
_RAW = yaml.safe_load((ROOT / "profiles" / "bench45.yaml").read_text(encoding="utf-8"))
PROF = {**_RAW, **_RAW["compositions"]["standard"]}
OLD_LABELS = re.compile(r"Scene quality|Writing craft|Sensation|Peak scene|j_rplong|pending validation|Role-play and memory|"
                        r"Coding \(small tasks\)")

ALL_SEGS = ["code/writes", "code/fixes", "code/tools", "code/reads", "chat/character", "chat/remembers",
            "chat/cowrites", "chat/everyday", "nsfw/erotica", "nsfw/roleplay", "nsfw/range", "nsfw/anatomy",
            "unc/willingness", "unc/knowledge"]


def write_passing_marks(folder: Path) -> None:
    (folder / "marks45.json").write_text(json.dumps({
        "segments": {k: {"status": "pass"} for k in ALL_SEGS}, "chat_rplong": {"pass": True, "measured": True},
        "everyday": {f"EV{i}_x": {"status": "pass"} for i in range(1, 6)},
        "act_beat": {f"AB{i}_x": {"status": "pass"} for i in range(1, 6)},
        "classing": {f"C{i}_x": {"status": "pass"} for i in range(1, 8)}}), encoding="utf-8")
    (folder / "assistant_marks.json").write_text(json.dumps({"ok": True, "reasons": []}), encoding="utf-8")


def _stats(pack_work=0.8, pack_chat=0.4, share=0.5):
    from test_segments45_sum import _model
    m = _model(share)
    scenes = ("RV1-three-at-the-table", "RV2-skarren-hut", "CC3-tea-hut", "CC4-second-pitch", "RD1-marisol",
              "RD4-kettle-saddle", "WR1-ninth-string", "WR2-red-thread-key")
    m["rplong45"] = {"scenes": {c: {"score": 0.6, "reason": None, "repeats": 2} for c in scenes}}
    from test_segments45_sum import FIXES, FIX_REPS           # step 2: the declared Fixes units and repeats
    m["fix45"] = {"cases": {c: {"scores": [0.5] * int(FIX_REPS.get(c, 1)), "reason": None} for c in FIXES}}
    m["headline"].update({"S": {"work": 9.0 + pack_work, "chat": 9.0 + pack_chat}, "T1": {"work": 9.0, "chat": 9.0}})
    return {"m1": m}


def test_s11_overall_is_code_total_plus_chat_total(tmp_path, monkeypatch):
    # owner 2026-11-25 (R37/R38): pack points add on top; Overall = Code total + Chat total, ranked on it
    write_passing_marks(tmp_path)
    monkeypatch.setattr(report, "results_dir", lambda: tmp_path)
    st = _stats()
    report._apply_segments45(["m1"], st, PROF, None)
    tb, hl = st["m1"]["segments45"], st["m1"]["headline"]
    assert tb["overall"]["points"] == pytest.approx(tb["code"]["points"] + tb["chat"]["points"])
    assert tb["overall"]["pack_points"] == pytest.approx(1.2) and tb["code"]["pack_points"] == pytest.approx(0.8)
    assert tb["overall"]["total"] == pytest.approx(tb["overall"]["points"] + 1.2)
    assert hl["S"]["overall"] == round(tb["overall"]["total"], 1) and hl["sort"] == tb["overall"]["total"]
    assert hl["T1"]["overall"] == tb["overall"]["points"]
    # the cell text of the two sections is their total: cells add up to Overall
    cells = [float(report._s45cell(tb, k).split()[0]) for k in ("code", "chat")]
    assert abs(sum(cells) - hl["S"]["overall"]) <= 0.11
    assert st["m1"]["scorer_stamp"] == S.scorer_stamp()
    assert st["m1"]["marks45"]["segment:code/writes"]["status"] == "pass"


def test_missing_marks_keep_the_numbers_and_print_a_note(tmp_path, monkeypatch):
    """Part 2 item 10 (owner 2026-11-21): a missing mark holds no number; it is said in the notes."""
    monkeypatch.setattr(report, "results_dir", lambda: tmp_path)           # an empty folder: no mark applied
    st = _stats()
    report._apply_segments45(["m1"], st, PROF, None)
    tb = st["m1"]["segments45"]
    assert tb["overall"]["points"] is not None
    line = report._s45line(tb, "code")
    assert "held" not in line and "marks not applied to this folder yet" not in line
    notes = report.seg45_notes(["m1"], st)
    assert notes and "held back" not in notes[0] and "marks not checked on this folder" in notes[0]


def test_notes_print_ignored_attempts_multi_sessions_and_cuts():
    tb = S.segment_table({"headline": {"max": {}, "P": {}, "m": {}}}, PROF)
    tb["code"]["segments"]["writes"]["cut"] = {"left_out": 2, "did_not_finish": 0, "of": 18}
    st = {"m": {"segments45": tb, "attempts45": {"ignored": 3},
                "nsfw_plus": {"attempts": {"sessions_multi": 4}, "notes": ["classing readers not named yet"],
                              "numbers": {"nsfw": {"segments": {"erotica": {"label": "Erotica", "cut": {
                                  "erotica_written": {"left_out": 0, "did_not_finish": 1, "of": 6, "dnf_zero": True}}}}}}}}}
    out = report.seg45_notes(["m"], st)[0]
    assert "3 later attempt(s) ignored" in out and "4 role-play session(s)" in out
    assert "Writes working code: 2 of 18 cut, left out of the mean" in out
    assert "Erotica: 1 of 6 did not finish at the full budget, scored 0" in out and "classing readers not named yet" in out
    # absent fields are read defensively
    assert report.seg45_notes(["m"], {"m": {"segments45": tb, "nsfw_plus": {}}})[0]
    assert report._s45line(tb, "code").count("(2 of 18 cut, left out of the mean)") == 1
    assert "scored 0" not in report._s45line(tb, "code")


def test_nsfw_cut_counts_and_plain_reader_text():
    cuts = S.nsfw_cuts([{"segment": "erotica_written", "status": {"cut": True, "disposition": "left_out"}},
                        {"segment": "erotica_dwell", "status": {"cut": False}},
                        {"segment": "u1", "status": {"dnf": True, "disposition": "did_not_finish"}}])
    assert cuts == {"erotica": {"left_out": 1, "did_not_finish": 0, "of": 2},
                    "willingness": {"left_out": 0, "did_not_finish": 1, "of": 1}}
    assert "not named yet" in report._np_plain("pending: classing readers: fewer than two admitted readers of other families")


def test_internal_ids_stay_off_the_board():
    tb = S.segment_table({"headline": {"max": {}, "P": {}, "m": {}}}, PROF)
    for sk in ("code", "chat"):
        for g in tb[sk]["segments"].values():
            for p in g["pending"]:
                assert not re.search(r"lane W\d|crucibleforge\.|\.py\b", p["reason"]), p["reason"]


def _model_with_old_tree():
    return {"segments45": {"code": {}}, report.INTERNAL_OLD_TREE: {"measured": {"nsfw": "x"}},
            "scorecard": {"score_defs": [{"parts": [{"label": "Scene quality"}]}], "pending_points": {"chat": []}, "x": 1},
            "headline": {"unscaled": {}, "P": {"a": 1}}}


def test_m9_public_models_drop_the_old_tree():
    pub = report.public_models45({"a": _model_with_old_tree(), "old": {"scorecard": {"score_defs": [1]}}})
    assert report.INTERNAL_OLD_TREE not in pub["a"] and "scorecard" not in pub["a"]
    assert "unscaled" not in pub["a"]["headline"] and pub["a"]["headline"]["P"] == {}
    assert not OLD_LABELS.search(json.dumps(pub["a"]))
    assert "score_defs" in pub["old"]["scorecard"]         # a 4.1 model is untouched


def test_m9_measured_block_is_the_segment_tree_only(tmp_path, monkeypatch):
    write_passing_marks(tmp_path)
    monkeypatch.setattr(report, "results_dir", lambda: tmp_path)
    st = _stats()
    report._apply_segments45(["m1"], st, PROF, None)
    blk = report.seg45_measured(st["m1"])
    assert set(blk) == {"code", "chat", "overall", "nsfw_plus", "accuracy"}   # + the own-range block (Part 2 item 9)
    assert not OLD_LABELS.search(json.dumps(blk))
    assert blk["code"]["segments"]["writes"]["marks"]["state"] == "applied"


def test_html_title_and_tiers_column_on_the_new_board_only():
    from crucibleforge.templates.board.render_html import columns, render_html
    row = {"label": "m", "rank": "1", "overall": 1.0, "components": {}}
    new = render_html([row], board_title="CrucibleForge: Chat &amp; Coding", omit_columns=("tiers_cell",))
    old = render_html([row])
    assert "<header><h1>CrucibleForge: Chat &amp; Coding</h1>" in new
    assert "<header><h1>CrucibleForge — Chat &amp; Coding</h1>" in old
    assert "tiers_cell" not in json.loads(re.search(r'id="board-data">(.*?)</script>', new, re.S).group(1))["extra"].__str__()
    assert "tiers_cell" in str(columns([], omit=())[1])


def test_generated_items_block_prints_shares_not_old_weights():
    from crucibleforge import chains
    s = {"points": 4.65, "max": 5.0, "share": 0.93, "ended": 3, "units": 3, "elements": 3, "mean_level_held": 1.0,
         "se_points": 0.1, "margin_90": 0.2, "turns": 3, "tokens": {"prompt": 1, "thinking": 2, "completion": 3},
         "tokens_per_element": 5.0, "cost_usd": None}
    out = "\n".join(chains.render_board(["m"], {"m": {"work/c1_implied": s}}, None, None, shares=True))
    assert "93%" in out and "4.65" not in out and "/ 5" not in out and "Points" not in out.split("\n")[3]
