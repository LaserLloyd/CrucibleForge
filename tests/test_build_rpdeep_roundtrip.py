"""E6: build_rpdeep.py keeps element metadata / retire records / revision stamps."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_rpdeep", ROOT / "scripts" / "build_rpdeep.py")
bld = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bld)

CUR = {c["id"]: c for c in json.loads((ROOT / "cases" / "rpdeep.json").read_text(encoding="utf-8"))}
IDS = ["CC3-tea-hut", "RV1-three-at-the-table", "RD1-marisol"]


def _lost(old, new, path=""):
    out = []
    for k, v in old.items():
        if k not in new:
            out.append(path + k)
        elif isinstance(v, dict) and isinstance(new[k], dict):
            out += _lost(v, new[k], path + k + ".")
    return out


def test_round_trip_loses_no_field():
    for cid in IDS:
        new, _ = bld.build_case(bld.SRC / cid, CUR[cid])
        assert new == CUR[cid], cid
        assert _lost(CUR[cid], new) == []
        for key in ("judge_elements", "checks"):
            ni = {e["id"]: e for e in new[key]}
            for e in CUR[cid][key]:
                assert e["id"] in ni, (cid, e["id"])
                assert _lost(e, ni[e["id"]]) == []


def test_retire_records_survive():
    new, _ = bld.build_case(bld.SRC / "RD1-marisol", CUR["RD1-marisol"])
    retired = [e for e in new["judge_elements"] if e.get("status") == "retire"]
    assert len(retired) == 11 and all(e.get("scored") is False for e in retired)


def test_without_existing_json_the_stamp_follows_case_revision(tmp_path):
    assert bld.merge_existing({"id": "X", "case_revision": "45.1"}, None)["element_standard"] == 45
    assert "element_standard" not in bld.merge_existing({"id": "X"}, None)
    assert "element_standard" not in bld.merge_existing({"id": "X", "case_revision": "41"}, None)


def test_new_source_rows_are_added_and_source_owns_non_metadata():
    old = {"id": "X", "judge_elements": [{"id": "A", "q": "kept", "class": "fact"}]}
    src = {"id": "X", "judge_elements": [{"id": "A", "q": "src", "turns": [1]}, {"id": "B"}]}
    out = bld.merge_existing(src, old)["judge_elements"]
    assert out[0] == {"id": "A", "q": "src", "turns": [1], "class": "fact"}
    assert out[1] == {"id": "B"}



def test_m3_source_edits_win_metadata_survives():
    cid = "RD1-marisol"
    old = json.loads(json.dumps(CUR[cid]))
    src_new, _ = bld.build_case(bld.SRC / cid, None)
    # simulate an old JSON whose source-owned keys are stale
    el = next(e for e in old["judge_elements"] if e.get("class") and e.get("q"))
    ck = next(c for c in old["checks"] if c.get("pattern"))
    el_q, ck_p = el["q"], ck["pattern"]
    el["q"] = "OLD question"
    ck["pattern"] = "OLDPATTERN"
    old["temperature"] = 0.123
    new, _ = bld.build_case(bld.SRC / cid, old)
    ne = next(e for e in new["judge_elements"] if e["id"] == el["id"])
    nc = next(c for c in new["checks"] if c["id"] == ck["id"])
    assert ne["q"] == el_q and nc["pattern"] == ck_p
    assert new["temperature"] == src_new["temperature"]
    assert ne["class"] == el["class"]                      # metadata survives
    assert {k: v for k, v in new.items()} == CUR[cid]
