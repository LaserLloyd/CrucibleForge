from pathlib import Path

from _db import answer, make


def test_sample():
    assert answer(make(Path("sample.sql").read_text())) == [
        ("Ana", "2026-03-01", 1, 40.0, 60.0), ("Ana", "2026-03-02", 1, 20.0, 20.0), ("Ben", "2026-03-01", 1, 91.0, 121.0)]
