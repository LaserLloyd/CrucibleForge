"""Fix pass C: one reader identity (the priced engine label), no Grok reader, DeepSeek rows local only, and the
chat-scene readers file loaded only when it is an index row."""
import shutil

import pytest

from crucibleforge import agree as AG
from crucibleforge import classing45 as C
from crucibleforge import profiles
from crucibleforge import rplong_mean as M

CASE = "CC3-tea-hut"
UNIT = "r|CC3-tea-hut|1"


def _row(sha="abc"):
    return {"case_id": CASE, "rubric": "rp_deep", "bench_run_id": "r", "repeat": 1,
            "judge": {"instrument": {"rubric_sha": sha}, "elements": {"results": [{"id": "a", "pass": True, "weight": 1}]}},
            "conversation": [{"content": "x"}]}


def test_default_readers_file_is_the_latest_indexed_addendum():
    # addendum b (pilot, indexed; lead ruling 2026-11-20: R-Q stays the DeepSeek Chat reader) is the latest
    assert M.READERS_CONFIG.name == "chat-readers-45-addendum-20261120b.yaml"
    R = M.load_readers()
    ids = [r.id for r in R["local"] + R["hosted"]]
    assert "grok" not in ids and not any("grok" in str(i) for i in ids)
    (h,) = R["hosted"]
    assert h.id == "deepseek-flash" and h.family == "deepseek" and M.display_name(h.id) == "DeepSeek v4 flash"


def test_no_grok_in_any_reader_list_or_default():
    prof = profiles.load_profile("bench45")
    rd = C.reader_roles(prof)
    assert not any("grok" in str(x).lower() for x in rd["set"] + rd["hosted_fallback"])
    named = C.named_readers()
    assert not any("grok" in str(d.get("model_id")).lower() for d in named.values())
    # addendum d (readers lane) names six hosted candidates before their C1; none is Grok
    assert {d.get("model_id") for d in named.values() if d.get("hosted")} == {
        "deepseek-flash", "kimi-k2.6", "openai/gpt-6.2-sol", "mistralai/mistral-large-4-0",
        "meta-llama/llama-4-maverick", "z-ai/glm-5.3", "cohere/command-a-plus"}


def test_deepseek_rows_get_local_readers_only():
    # addendum b names R-Q (qwen heretic, local) for DeepSeek rows; the lead ruled it valid for Chat (2026-11-20)
    r = M.pinned_reader("deepseek", M.load_readers())
    assert r.family == "qwen" and r.id == "qwen3.8-27b-ultra-heretic-nvfp4"
    # a qwen row may use the hosted reader
    assert M.pinned_reader("qwen", M.load_readers()).id == "deepseek-flash"


def test_an_edited_copy_of_the_readers_file_is_refused(tmp_path):
    cal = tmp_path / "calibration"
    cal.mkdir()
    p = cal / M.READERS_CONFIG.name
    shutil.copy(M.READERS_CONFIG, p)
    assert M.load_readers(p)["hosted"][0].id == "deepseek-flash"     # same bytes: an index row
    p.write_text(p.read_text(encoding="utf-8").replace("deepseek-flash", "grok"), encoding="utf-8")
    with pytest.raises(ValueError, match="not a row of MARKS-INDEX-45"):
        M.load_readers(p)


def test_records_imported_under_an_engine_alias_match_the_pin(tmp_path, monkeypatch):
    row = _row("abc")
    rec = {"part": AG.LONG_PART, "model": "m", "unit": UNIT, "row_sha": AG.row_sha(row),
           "reader": "deepseek-v4-flash+think", "score_reader": 0.5}
    monkeypatch.setattr(AG, "load_records", lambda folder, key: [rec])
    pin = M.pinned_reader("qwen", M.load_readers())
    out = M.import_agree_records(tmp_path, pin, "deepseek-v4-flash", rows=[row])
    assert out[0]["reader_stamp"]["model"] == "deepseek-flash"
    u = M.scene_scores([row], out, label="m", cases={}, graded_family="qwen")[UNIT]
    assert u["status"] == "ok", u
