"""Fix pass C: the scorer stamp covers the profile and chains.py and is printed in the board footer ("scored with",
"re-scored under" when the published stamp differs); a malformed marks file is never a pass."""
import json

from crucibleforge import report, segments45 as S


def test_the_stamp_covers_the_profile_and_chains():
    assert "chains.py" in S.SCORER_FILES and "profiles/bench45.yaml" in S.SCORER_DATA


def test_the_stamp_changes_with_the_profile(monkeypatch, tmp_path):
    from pathlib import Path
    real = Path.read_bytes
    S.scorer_stamp.cache_clear()
    a = S.scorer_stamp()

    def fake(self):
        b = real(self)
        return b + b"#x" if self.name == "bench45.yaml" else b
    monkeypatch.setattr(Path, "read_bytes", fake)
    S.scorer_stamp.cache_clear()
    try:
        assert S.scorer_stamp() != a
    finally:
        monkeypatch.undo()
        S.scorer_stamp.cache_clear()


def test_the_footer_prints_scored_with_and_re_scored_under():
    st = {"m": {"scorer_stamp": "aaaa"}}
    assert report.scorer_stamp_note(["m"], st, {}) == "Numbers scored with aaaa (report-time scorer stamp)."
    t = report.scorer_stamp_note(["m"], st, {"m": "bbbb"})
    assert t.startswith("Numbers re-scored under aaaa (published under bbbb)")
    assert report.scorer_stamp_note(["m"], {"m": {}}, {}) is None


def test_published_stamps_are_read_from_a_report(tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"models": {"m": {"scorer_stamp": "cc"}, "n": {}}}), encoding="utf-8")
    assert S.published_stamps(p) == {"m": "cc"} and S.published_stamps(tmp_path / "none.json") == {}


def test_malformed_marks_are_never_a_pass(tmp_path):
    (tmp_path / "marks45.json").write_text(json.dumps({"segments": {"code/writes": {"pass": "false"},
                                                                     "code/tools": {"ok": 1}}}), encoding="utf-8")
    m = S.load_marks(tmp_path)
    assert m["segment:code/writes"]["status"] == "missing" and m["segment:code/tools"]["status"] == "missing"
    for bad in ([1, 2], 3, {"segments": [1]}):
        (tmp_path / "marks45.json").write_text(json.dumps(bad), encoding="utf-8")
        assert all(v["status"] == "missing" for v in S.load_marks(tmp_path).values())
