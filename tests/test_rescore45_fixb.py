"""scripts/rescore45.py never reports a clean stale check over zero models (S9)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rescore45", ROOT / "scripts" / "rescore45.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def test_zero_models_is_not_every_row_current():
    msg, stale, ok = R.stale_summary([])
    assert not ok and "every stored row current" not in msg and "NOTHING VERIFIED" in msg


def test_clean_and_stale_lines():
    msg, stale, ok = R.stale_summary(["m1  0 stale row(s)"])
    assert ok and "every stored row current" in msg
    msg, stale, ok = R.stale_summary(["m1  0 stale row(s)", "m2  3 stale row(s)"])
    assert not ok and stale == ["m2  3 stale row(s)"]


def test_main_exits_nonzero_when_no_model_was_checked(tmp_path, monkeypatch):
    res, out = tmp_path / "res", tmp_path / "out"
    res.mkdir()

    class P:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: P())
    monkeypatch.setattr(sys, "argv", ["rescore45.py", "--results", str(res), "--out", str(out)])
    assert R.main() == 3
