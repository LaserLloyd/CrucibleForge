"""Stamp-2 fix pass (lead ruling): pack scene rows (the parts with ``pack_segment``: judged set-1 scenes and the
generated sceneladder / session-ledger / istack levels) never enter an old-tree component mean. One predicate
(report.is_pack_part / report.pack_case_ids) serves the weights and the rows."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from crucibleforge import profiles, report

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]
FOLDER = Path(os.environ.get("CF_LAUNCH_ROWS") or "launch-rows-not-set.jsonl")     # a real run's transcripts_<label>.jsonl


@pytest.fixture
def prof():
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    report._CASES_BY_ID = None
    return p


def test_one_predicate_serves_weights_and_rows(prof):
    parts = report.pack_parts(prof)
    # step 2 (2026-11-21): the Fixes pack part (work/c3_fix, gated by enginepacks45) is a pack part too
    assert parts == {"chat/pk_character", "chat/pk_remembers", "chat/pk_cowrites", "work/c3_fix"}
    assert report.is_pack_part(prof, "chat", "pk_character") and not report.is_pack_part(prof, "chat", "h2_state")
    ids = report.pack_case_ids(prof)
    assert {"LRV1-pilots-mess", "LCC1-quiet-hours", "LWR3-twelve-days", "SCENELADDER-r6-1", "SCENELADDER-r7-2",
            "SESSION_LEDGER-r4-1", "SESSION_LEDGER-r5-2", "ISTACK-write-r7-1", "ISTACK-write-r8-2"} <= ids
    assert "RV1-three-at-the-table" not in ids and "CC3-tea-hut" not in ids
    from crucibleforge import packs45
    assert set(packs45.PACK_SCENES) <= ids
    src = Path(report.__file__).read_text(encoding="utf-8")
    assert src.count('get("pack_segment")') == 1, "pack_segment must be read in ONE place (the predicate)"


def _outputs(rows):
    cfg = {"_profile": "bench45", "models": []}
    s = report.model_stats("m", cfg, rows=rows, meta={})
    p = report._tier_profile(cfg)
    card = report.scorecard(s, report.scoring_config(cfg))
    s["scorecard"] = card
    ps = report.points_scorecard(s, report.scoring_config(cfg), p)
    return json.loads(json.dumps({"rpdeep": s["rpdeep"], "total": card["total"], "chat": card["chat"],
                                  "coding": card["coding"], "components": card["components"],
                                  "internal_old_tree": {"measured": report.points_measured({**s, "scorecard": ps})},
                                  "points_scorecard": ps}, default=str, sort_keys=True))


@pytest.mark.slow
@pytest.mark.skipif(not FOLDER.is_file(), reason="set CF_LAUNCH_ROWS to a real run's transcripts file to run this")
def test_a_judged_pack_scene_row_moves_no_old_tree_value(prof):
    rows = [json.loads(x) for x in FOLDER.read_text(encoding="utf-8").splitlines() if x.strip()]
    sub = [r for r in rows if r.get("category") in ("rpdeep", "coding")]
    src = [r for r in sub if r.get("case_id") == "RV1-three-at-the-table" and r.get("bench_run_id") == "bb53fe61"
           and r.get("repeat") == 1]
    assert src
    packs = []
    for case in ("LRV1-pilots-mess", "SCENELADDER-r6-1"):          # a judged set-1 scene and a generated level
        for r in src:
            q = copy.deepcopy(r)
            q["case_id"] = case
            packs.append(q)
    a, b = _outputs(sub), _outputs(sub + packs)
    assert "LRV1-pilots-mess" in report._cases_by_id(), "the pack case must be loadable here, or the test proves nothing"
    assert a == b
