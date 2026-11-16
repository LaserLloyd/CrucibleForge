"""Golden 4.1 board (review F1, the guard against recurrence): real public 4.1
rows scored under the default ``bench`` profile must give exactly the scores
dev gave before any 4.5 change (tests/fixtures/board41/expected.json).

The fixture holds one row set per model (public cases only):

- qwen2.5-vl-7b-caption WR1: an rpdeep session whose facts the judge zeroed on
  a lone out-of-character flag (the 4.5 OOC rescue must not touch it on bench);
- claude-haiku-4-5 UX3: an element that failed only on "quote not in the
  reply" (the 4.5 board-time quote recheck must not flip it on bench);
- kimi-k3 WL2: an NSFW request the provider's content filter stopped;
- deepseek-r1-0528-8b TZ19: an errored (transport) row;
- gpt-6-1-terra RD4: an rpdeep fact whose stored quote verifies only under the
  newer quote normaliser (markdown emphasis dropped), which bench must not use
  at board time.

A 4.5 change that is meant to reach the 4.1 board is a 4.1.x scoring change
with a delta table (scripts/board_ab.py over a full snapshot), and only then
is expected.json regenerated."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crucibleforge import config, report

FIX = Path(__file__).parent / "fixtures" / "board41"
ROWS = json.loads((FIX / "rows.json").read_text(encoding="utf-8"))


@pytest.fixture
def board41(tmp_path, monkeypatch):
    # one rows.json ({label: [rows]}): *.jsonl is git-ignored repo-wide
    for label, rows in ROWS.items():
        (tmp_path / f"transcripts_{label}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    return tmp_path


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= 1e-9


def test_fixture_is_public_and_small():
    ids = {r["case_id"] for rows in ROWS.values() for r in rows}
    from crucibleforge.config import load_cases
    public = {c["id"] for c in load_cases()}
    assert ids and ids <= public
    assert sum(p.stat().st_size for p in FIX.iterdir()) < 200_000


@pytest.mark.archive_default
def test_bench_board_reproduces_the_4_1_scores(board41):
    want = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))["board"]
    labels, stats, _ = report.board_stats(None, None)
    assert sorted(labels) == sorted(want)
    for l in labels:
        card = stats[l]["scorecard"]
        assert _close(card.get("total"), want[l]["total"]), l
        assert card.get("labels") == want[l]["labels"], l
        for score, parts in want[l]["parts"].items():
            for part, v in parts.items():
                got = (card.get("parts") or {}).get(score, {}).get(part)
                assert _close(got, v), f"{l} {score}/{part}: {got} != {v} (4.1)"


@pytest.mark.archive_default
def test_bench_board_keeps_stored_element_verdicts(board41):
    """Board-time re-scoring on bench is the 4.1.0 release's ``legacy`` quote recheck and nothing
    else: an element reads as the judge stored it, except one that failed ONLY because its quote
    did not verify and verifies now (fix-d: the old assertion predated 4.1.0's recheck; it fails
    at 1671f17 itself)."""
    stored = [r for r in ROWS["claude-haiku-4-5"] if (r.get("judge") or {}).get("elements")]
    rows, _ = report.board_view("claude-haiku-4-5", None)
    assert rows
    for r in rows:
        res = r["judge"]["elements"]["results"]
        src = next(s for s in stored if s.get("ts") == r.get("ts"))
        for e, o in zip(res, src["judge"]["elements"]["results"]):
            if (e["id"], e["pass"], e["detail"]) == (o["id"], o["pass"], o["detail"]):
                continue
            assert o["pass"] is False and "quote not in the reply" in o["detail"]
            assert e["pass"] is True and e["detail"].endswith("quote verified at board time")
