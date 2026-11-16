"""version.case_table over an explicit cases dir reads THAT tree's ladder/ only (2026-11-14): a one-case
test dir used to hash the live ladder too, and since 20fb58a that includes FC2's six combsyn level-7
instances, minutes each to enumerate under any release seed but the frozen 4.5.0 (test_gen_registry's
"T1"/"T9"): test_non_generated_hash_ignores_release_seed ran past 30 minutes."""
from __future__ import annotations

import json
import time

import pytest

from crucibleforge import gen, version


def _tree(root, cases, ladder=None):
    (root / "coding.json").write_text(json.dumps(cases), encoding="utf-8")
    if ladder:
        d = root / "ladder" / "work" / "code"
        d.mkdir(parents=True)
        (d / "set-1.json").write_text(json.dumps({"level": 1, "cases": ladder}), encoding="utf-8")
    return root


def test_a_one_case_dir_never_reads_the_live_ladder(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(gen, "content_digest", lambda c: called.append(c["id"]) or "x")
    gen.set_release_seed("T9")                    # a seed the frozen combsyn data does not cover
    try:
        t0 = time.monotonic()
        table = version.case_table(_tree(tmp_path, [{"id": "PLAIN", "prompt": "hi"}]))
        took = time.monotonic() - t0
    finally:
        gen.set_release_seed(None)
    assert set(table) == {"PLAIN"} and called == []     # no live pack question, nothing materialised
    assert took < 10, f"case_table of a one-case dir took {took:.1f} s"


def test_the_given_trees_own_ladder_is_read(tmp_path):
    table = version.case_table(_tree(tmp_path, [{"id": "PLAIN", "prompt": "hi"}],
                                     ladder=[{"id": "PK-1", "category": "coding", "prompt": "x"}]))
    assert set(table) == {"PLAIN", "PK-1"}
    # an explicit ladder_dirs still wins
    assert set(version.case_table(tmp_path, ladder_dirs=[])) == {"PLAIN"}


@pytest.mark.live_ladder
@pytest.mark.archive_default
def test_the_live_tree_still_reads_the_live_ladder():
    ids = set(version.case_table())
    assert {"FC2-combsyn-L7-p1"} <= ids                # the live pack questions, as before
    assert set(version.case_table(version._CASES_DIR)) == ids   # the live dir given explicitly: the same
