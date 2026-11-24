"""The judge store saves a verdict whose text holds a lone surrogate.

2026-11-24: a hosted model's reply was cut in the middle of an emoji. The judge answered, but writing the verdict
to the store raised UnicodeEncodeError ("surrogates not allowed"), the role-play sessions stayed pending and the
model could not be ranked. The record is now written with ASCII escapes in that case, which loads back identically.
"""
from crucibleforge import judge45


def test_a_lone_surrogate_is_stored_and_loads_back_identically(tmp_path):
    text = "she smiled \ud83d and left"                     # half of an emoji
    path = tmp_path / "judge45" / "store.jsonl"
    st = judge45.JudgeStore(path)
    st.put("rp", "judge-x", "sha", text, {"class": "ok", "quote": text})
    again = judge45.JudgeStore(path)
    assert again.get("rp", "judge-x", "sha", text) == {"class": "ok", "quote": text}


def test_ordinary_text_is_still_written_unescaped(tmp_path):
    path = tmp_path / "store.jsonl"
    judge45.JudgeStore(path).put("rp", "judge-x", "sha", "café 🙂", {"class": "ok"})
    assert "café 🙂" in path.read_text(encoding="utf-8")
