"""Stamp-2 fix pass, finding 7: the attest gate reads bank ``items`` and checks every bank a generated set names."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("attest_check", ROOT / "scripts" / "attest_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def att(by, verdict="PASS"):
    return {"by": by, "date": "2026-11-20", "verdict": verdict, "reasons": "adult, fictional"}


def item(i, *atts):
    return {"id": i, "attest": list(atts)}


def put(d, rel, obj):
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def test_a_bank_item_without_a_grok_record_is_caught(tmp_path):
    m = load()
    put(tmp_path, "packs45/range-asks.json", {"items": [item("PK-1", att("deepseek"), att("grok")),
                                                        item("PK-2", att("deepseek"))]})
    out = m.problems(tmp_path)
    assert len(out) == 1 and "PK-2" in out[0] and "grok" in out[0]


def test_a_fail_verdict_on_a_bank_item_is_caught(tmp_path):
    m = load()
    put(tmp_path, "packs45/range-asks.json", {"items": [item("PK-1", att("deepseek"), att("grok", "FAIL"))]})
    out = m.problems(tmp_path)
    assert any("PK-1" in x and "FAIL" in x for x in out)


def test_every_bank_a_generated_set_names_was_scanned(tmp_path):
    m = load()
    put(tmp_path, "n7_taboo/set-1.json", {"generator": {"family": "nsfwpack45", "variant": "taboo", "rungs": [1]}})
    put(tmp_path, "other.json", {"cases": [{"id": "X", "meta": {"attest": [att("grok"), att("deepseek")]}}]})
    out = m.problems(tmp_path)
    assert any("range-asks.json" in x and "n7_taboo/set-1.json" in x for x in out)
    put(tmp_path, "packs45/range-asks.json", {"items": [item("PK-1", att("deepseek"), att("grok"))]})
    assert m.problems(tmp_path) == []


@pytest.mark.live_ladder   # reads the real tree: since 2026-11-21 the knowledge pack is wired and draws the unc_know bank
def test_the_real_nsfw_tree_is_clean_and_a_dropped_grok_record_is_caught(tmp_path):
    m = load()
    src = ROOT / "cases" / "ladder" / "nsfw"
    assert m.problems(src) == []
    dst = tmp_path / "nsfw"
    shutil.copytree(src, dst)
    f = dst / "packs45" / "write.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    first = d["items"][0]
    first["attest"] = [a for a in first["attest"] if a["by"] != "grok"]
    f.write_text(json.dumps(d), encoding="utf-8")
    out = m.problems(dst)
    assert any(first["id"] in x and "grok" in x for x in out)
