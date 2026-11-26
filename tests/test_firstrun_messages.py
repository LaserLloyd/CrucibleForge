"""The "If something goes wrong" table in docs/FIRST-RUN-DOCKER.md and the messages the code prints agree: every
message has a row with its exit code, and every row with an exit code names a message that still exists."""
from __future__ import annotations

import re
from pathlib import Path

from crucibleforge import firstrun as fr

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "FIRST-RUN-DOCKER.md"
SOURCES = [ROOT / "crucibleforge" / "firstrun.py", ROOT / "crucibleforge" / "quicktest.py",
           ROOT / "crucibleforge" / "viewserver.py"]


def _rows() -> list[tuple[str, str]]:
    text = PAGE.read_text(encoding="utf-8")
    table = text[text.index("## If something goes wrong"):]
    out = []
    for line in table.splitlines():
        m = re.match(r"\|\s*`([^`]+)`\s*\|\s*([0-9-]+)\s*\|", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def test_every_message_has_a_row_with_its_exit_code():
    rows = _rows()
    for name, head in fr.HEADLINES.items():
        hits = [code for msg, code in rows if head in msg]
        assert hits, f"{name}: no row in the page's table names {head!r}"
        assert str(fr.CODES[name]) in hits, f"{name}: the page says exit {hits}, the code uses {fr.CODES[name]}"


def test_every_row_with_an_exit_code_is_a_message_the_code_still_prints():
    heads = list(fr.HEADLINES.values()) + list(fr.NOTES.values())
    for msg, code in _rows():
        if code == "-":
            continue
        assert any(h in msg for h in heads), f"the page lists {msg!r}, which no message prints any more"


def test_every_headline_is_used_by_the_code():
    src = "\n".join(p.read_text(encoding="utf-8") for p in SOURCES)
    for name in fr.HEADLINES:
        assert re.search(rf"HEADLINES\[[\"']{name}[\"']\]|fail\([\"']{name}[\"']", src), \
            f"{name} is in the catalogue but no code prints it"


def test_exit_codes_are_distinct_per_kind_of_failure():
    by_code: dict[int, set] = {}
    for name, code in fr.CODES.items():
        by_code.setdefault(code, set()).add(name.split("_")[0])
    # codes are shared only inside one family (KEY_*, LOCAL_* model lookups, the NO_KEY/KEY_EMPTY pair ...)
    assert by_code[29] == {"NETWORK", "DNS", "TLS"}
    assert len({c for c in fr.CODES.values()}) >= 18
