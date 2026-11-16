"""The 4.1 public board data is frozen (lead 2026-11-15, after a 4.5 field leaked into the 4.1
efficiency dict): the golden 4.1 rows (tests/fixtures/board41) rendered as a PUBLIC board under the
default ``bench`` profile must give, byte for byte, the board data a pre-4.5-PUBOUT commit
(af52930) gave: the JSON inside report.html's ``board-data`` script (what the live site reads) and
the public report.json minus its ``generated`` timestamp. Any 4.5 field belongs on a 4.5 profile's
board only. A deliberate 4.1 change regenerates tests/fixtures/board41_public/*.json with a delta
table (see test_board41_golden.py)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from crucibleforge import config, report

FIX = Path(__file__).parent / "fixtures" / "board41"
#: the goldens live apart: board41/ is held under 200 kB by test_board41_golden
GOLD = Path(__file__).parent / "fixtures" / "board41_public"
ROWS = json.loads((FIX / "rows.json").read_text(encoding="utf-8"))
_BOARD_DATA = re.compile(r'<script[^>]*id="board-data"[^>]*>(.*?)</script>', re.S)


def render_public_board41(tmp_path: Path, monkeypatch) -> tuple[str, str]:
    """(board-data JSON, report.json without ``generated``), both canonical (sorted keys)."""
    res = tmp_path / "results"
    res.mkdir()
    for label, rows in ROWS.items():
        (res / f"transcripts_{label}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(config, "RESULTS_DIR", res)
    out = tmp_path / "public"
    report.generate(None, write=True, public=True, out_dir=out, cfg={})
    page = (out / "report.html").read_text(encoding="utf-8")
    m = _BOARD_DATA.search(page)
    assert m, "report.html has no board-data script"
    import html as _html
    bd = json.loads(_html.unescape(m.group(1)) if m.group(1).lstrip().startswith("&") else m.group(1))
    rj = json.loads((out / "report.json").read_text(encoding="utf-8"))
    rj.pop("generated", None)
    dump = lambda o: json.dumps(o, sort_keys=True, indent=1, ensure_ascii=False, default=str) + "\n"  # noqa: E731
    return dump(bd), dump(rj)


def test_the_4_1_public_board_data_is_byte_identical(tmp_path, monkeypatch):
    bd, rj = render_public_board41(tmp_path, monkeypatch)
    assert bd == (GOLD / "public_board_data.json").read_text(encoding="utf-8"), "4.1 board-data drifted"
    assert rj == (GOLD / "public_report.json").read_text(encoding="utf-8"), "4.1 public report.json drifted"
