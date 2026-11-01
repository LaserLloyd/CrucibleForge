from pathlib import Path

from _db import answer, make


def test_sample_table():
    conn = make(Path("sample.sql").read_text())
    assert answer(conn) == [("2026-01", "Ben", 7000), ("2026-02", "Ana", 2500), ("2026-02", "Cy", 2500),
                            ("2026-03", None, 0), ("2026-04", "Cy", 1200), ("2026-05", None, 0),
                            ("2026-06", None, 0)]
