"""Fix pass C (A1): a realigned model's public output carries the segment tree only. No old part points or maxima,
no old NSFW part ids or labels, no pack-inclusive part values, in report.json, the HTML board data or report.md's
Ladders and Generated items."""
import json
import re

from crucibleforge import report
from test_board_fixb import PROF, _stats, write_passing_marks

OLD = re.compile(r"(?<![:\w])(?:work|chat|nsfw)/[a-z][a-z0-9_]*|\bn[1-8][ab]?_[a-z]+|\bu[1-4]_(?:will|straight|know|"
                 r"ground)\b|Scene quality|Writing craft|Sensation|Peak scene|\bc1_implied\b|\bj_rpsession\b|"
                 r"\bh2_state\b|\bc5_tools\b|adult page")
OLD_NSFW = ("n1_scene", "n2_peak", "n3_way", "n4_straight", "n5_sense", "n6a_craft", "n6b_brief", "n7_taboo",
            "n8_bodies", "u1_will", "u2_straight", "u3_know", "u4_ground")


def _realigned_with_old_nsfw(tmp_path, monkeypatch):
    write_passing_marks(tmp_path)
    monkeypatch.setattr(report, "results_dir", lambda: tmp_path)
    st = _stats()
    m = st["m1"]
    hl = m["headline"]
    for k in OLD_NSFW:                               # an old-tree NSFW part tree on the stored row
        for f, v in (("P", 0.7), ("S", 0.7), ("max", 2.0), ("m", 0.1)):
            hl.setdefault(f, {})[f"nsfw/{k}"] = v
    hl.setdefault("P", {})["work/c1_implied"] = 5.54        # old part value with packs inside
    hl.setdefault("max", {})["work/c1_implied"] = 5.0
    m["scorecard"] = {"parts": {"nsfw": {k: {"label": "Scene quality", "points": 1.0} for k in OLD_NSFW}},
                      "subtotals": {"nsfw": 9.0}, "weights": {"work": {"c1_implied": 5.0}},
                      "columns": ["Writing craft"], "components": {"Sensation": 50.0},
                      "score_defs": [{"key": "nsfw", "parts": [{"key": "n1_scene", "label": "Peak scene"}]}]}
    m["chain_stats"] = {f"nsfw/{k}": {"points": 1.0} for k in OLD_NSFW}
    m["nsfw"] = {"pass_rate": 0.5}
    report._apply_segments45(["m1"], st, PROF, None)
    m["measured"] = report.seg45_measured(m)
    return st


def test_public_report_json_carries_only_the_segment_tree(tmp_path, monkeypatch):
    st = _realigned_with_old_nsfw(tmp_path, monkeypatch)
    pub = report.public_models45(st)["m1"]
    text = json.dumps(pub, default=str)
    assert not OLD.search(text), OLD.search(text).group(0)
    assert set(pub["headline"]["P"]) <= {"work", "chat", "overall"}
    assert "scorecard" not in pub and "chain_stats" not in pub and "nsfw" not in pub
    assert pub["measured"]["code"]["segments"]["writes"]["label"]
    assert "units" not in json.dumps(pub["segments45"])


def test_html_component_maximum_is_the_tree_points():
    st = {"m": {"segments45": {"code": {"segments": {"writes": {"units": [{"id": "c1_implied", "weight": 3.2}]}}}}}}
    assert report._tree_unit_weights(st) == {"c1_implied": 3.2}


def test_a_4_5_render_drops_the_4_1_adult_page_comments_and_the_script_keeps_them():
    from pathlib import Path
    js = (Path(report.__file__).parent / "templates" / "board" / "script.js").read_text(encoding="utf-8")
    assert "// rc3 adult page:" in js                     # the 4.1 render reads it word for word
    out = report.SCRIPT_OLD_COMMENT_RE.sub("", js)
    assert "adult page" not in out.lower() and "function sval" in out and "function subTag" in out


def test_generated_items_rows_of_old_parts_are_recognised():
    assert report._gen_row_part("| m | nsfw/n1_scene | 3/3 | 90% |") == "n1_scene"
    assert report._gen_row_part("| m | work/c1_implied | 3/3 |") == "c1_implied"
    assert report._gen_row_part("| Model | Part | Ended |") is None
