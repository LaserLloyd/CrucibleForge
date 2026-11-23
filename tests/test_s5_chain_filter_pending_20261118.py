"""S5 (fix round 2026-11-18, AUDIT-HARNESS F5): chain parts decided ANY judged unit whose row the provider's content
filter stopped, as a refusal (0), Role-play sessions included. A filter stop is the model side's refusal only on the
adult page; on every other part it is infrastructure, like ``scoring45._row_empty`` says: the unit stays pending."""
from __future__ import annotations

import copy

import pytest

from crucibleforge import chains, config

from test_rpladder_board_20261115 import PART, ROWS, _load, _stats, _with_ladder

pytestmark = pytest.mark.live_ladder


def _filtered(rows, uid):
    out = copy.deepcopy(rows)
    for r in out:
        ch = r.get("chain") or {}
        if ch.get("id") != uid:
            continue
        if r.get("conversation"):
            r["finish_reason"] = "content_filter"
            r["conversation"][-1]["content"] = ""
            r["response"] = ""
    return out


def _uid(rows):
    return next(r["chain"]["id"] for r in rows
                if (r.get("chain") or {}).get("part") == PART and r.get("conversation"))


@pytest.mark.parametrize("placed", [False, True])
def test_a_filtered_session_stays_pending(monkeypatch, placed):
    p = _load("standard", monkeypatch)
    base = config.dedupe_rows(copy.deepcopy(ROWS["deepseek-flash"]))
    uid = _uid(base)
    rows = _filtered(base, uid)
    if placed:
        rows = _with_ladder(rows, p, pos=2.0)
    cs = _stats(p, rows)["chain_stats"][PART]
    u = cs["per_chain"][uid]
    assert u["ended"] is False and not u.get("elements"), u
    assert not cs["complete"]


def test_decided_without_judge_only_on_the_adult_page():
    row = {"finish_reason": "content_filter", "response": ""}
    assert chains._decided_without_judge(row, adult=True) is True
    assert chains._decided_without_judge(row, adult=False) is False
    assert chains._decided_without_judge({"finish_reason": "stop", "response": "x"}, adult=True) is False
