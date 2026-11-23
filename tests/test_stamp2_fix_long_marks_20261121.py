"""Stamp-2 fix pass, finding 1: pack reader records never enter the standard long-scene mark."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from crucibleforge import agree as AG  # noqa: E402
from crucibleforge import rplong_marks as K  # noqa: E402


def _rec(case, run, j, r, model="m", reader="deepseek-flash+think"):
    return {"part": "chat/j_rplong", "model": model, "unit": f"{run}|{case}|1", "row_sha": "x", "reader": reader,
            "score_judge": j, "score_reader": r, "decisions": [[True, True]], "decision_ids": ["a"], "weight": 1.0,
            "graded_family": "qwen"}


def _std():
    return [_rec(c, r, 0.8, 0.79) for c in ("CC3-tea-hut", "RD1-marisol") for r in "abcdefgh"]


def _packs():
    return [_rec(c, "p", 0.95, 0.4) for c in ("LRV1-pilots-mess", "LCC1-quiet-hours", "LRV2-x")]


def test_standard_long_keeps_only_the_eight_scenes():
    recs = _std() + _packs()
    kept = AG.standard_long(recs)
    assert kept == _std()


def test_check_is_identical_with_and_without_pack_records():
    mk = K.load_marks()
    a = K.check(_std(), mk)
    b = K.check(_std() + _packs(), mk)
    assert a["judge_term_share"] == b["judge_term_share"]
    assert a["fails"] == b["fails"] and a["pass"] == b["pass"]
    assert a["groups"] == b["groups"]


def test_check_script_store_with_pack_records_equals_standard_only(tmp_path):
    import check_rplong_marks45 as S
    out = []
    for name, recs in (("std", _std()), ("both", _std() + _packs())):
        res = tmp_path / name
        (res / "readers45").mkdir(parents=True)
        (res / "readers45" / "m.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
        rc = S.main(["--results", str(res), "--no-write"])
        out.append(rc)
    assert out[0] == out[1]


def test_check_judge_marks_folder_records_drop_pack_units(tmp_path):
    import check_judge_marks as J
    assert hasattr(J, "AG") and J.AG.standard_long is AG.standard_long
