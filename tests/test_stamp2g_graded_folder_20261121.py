"""Stamp-2 batch G: the stored reader records stay valid across the G1/G2 fix (store keyed by content sha).

Runs on a COPY of a graded launch folder named by ``MB45_STAMP2G_FOLDER`` (skipped without it; the folders hold
adult text and are never part of the repo). Offline only: a reader is never called (``classing45.offline_chat``); a
missing line surfaces as ``ReaderMissing`` = "pending readers". Every write / dwell / taboo / U1 unit (53 per model)
keeps its (ask, region), so none of them needs a new read under the readers the folder admits.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from crucibleforge import adult45, classing45 as C, lineage, profiles

FOLDER = os.environ.get("MB45_STAMP2G_FOLDER")
pytestmark = [pytest.mark.skipif(not FOLDER, reason="MB45_STAMP2G_FOLDER (a copy of a graded folder) not set"),
              pytest.mark.shipping_default, pytest.mark.live_ladder]
SINGLE = ("write", "dwell", "taboo", "uask")


def test_single_reply_units_need_no_new_read():
    res = Path(FOLDER)
    tf = next(res.glob("transcripts_*.jsonl"))
    label = tf.name[len("transcripts_"):-len(".jsonl")]
    prof = profiles.load_profile("bench45")
    prompt = C.load_prompt(prof)
    # the readers as the store was filled (no C6 removal applied): proves the store keys, not the pick
    readers = C.pick_readers(lineage.family(label), prof, c1=C.load_c1(res), prompt_sha=prompt[1])
    store = C.ReaderStore(res / "readers45" / f"{label}.jsonl")
    units = [u for u in C.units_from_rows(C.board_rows(tf)) if u["variant"] in SINGLE]
    assert len(units) == 53
    missing = []
    for u in units:
        if C.unit_pre_read(u) is not None or adult45.detector(u["reply"]):
            continue
        try:
            C.class_unit(u["ask"], u["reply"], u["sexual"], lineage.family(label), readers, store, prompt=prompt,
                         chat=C.offline_chat)
        except C.ReaderMissing:
            missing.append(u["unit_id"])
    assert missing == []
