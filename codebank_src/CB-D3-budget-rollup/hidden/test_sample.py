from pathlib import Path

from _db import answer, make


def test_sample_month():
    conn = make(Path("sample.sql").read_text())
    assert answer(conn, "2026-03") == [
        ("Home", 2000, 50500, None, None, 0), ("Home > Energy", 16000, 16000, 15000, -1000, 1),
        ("Home > Food", 0, 32500, 46000, 13500, 0), ("Home > Food > Groceries", 28000, 28000, 30000, 2000, 0),
        ("Home > Food > Takeaway", 4500, 4500, None, None, 0), ("Kids", 0, 0, None, None, 0)]
