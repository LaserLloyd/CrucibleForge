"""A pack question's hash must not depend on where the checkout lives (D, rc2 board 2026-11-14):
``ladder_pack`` (the set file's ABSOLUTE path) went into gen.content_digest, so a PK- row read
current only under the tree path it ran from. Byte-identical pack files under two roots must give
one digest and one case hash."""
from __future__ import annotations

import shutil

import pytest

from crucibleforge import gen, ladder, version
from crucibleforge.config import ROOT_DIR

PACK = ("chat", "character")           # a shipped pack of generated questions (session_state)


def _generated(root):
    return {c["id"]: c for c in ladder.inline_cases([root]) if gen.is_generated(c)}


def _copy(dst):
    shutil.copytree(ROOT_DIR / "cases" / "ladder" / PACK[0] / PACK[1], dst / PACK[0] / PACK[1])
    return dst


@pytest.mark.live_ladder
def test_the_same_pack_under_two_roots_gives_one_digest(tmp_path):
    a = _copy(tmp_path / "tree-a" / "cases" / "ladder")
    b = _copy(tmp_path / "elsewhere" / "x" / "ladder")
    ca, cb = _generated(a), _generated(b)
    assert ca and ca.keys() == cb.keys()
    some = sorted(ca)[:6]
    for i in some:
        assert ca[i]["ladder_pack"] != cb[i]["ladder_pack"]               # the paths differ
        assert gen.content_digest(ca[i]) == gen.content_digest(cb[i]), i
        assert version._content_view(ca[i]) == version._content_view(cb[i]), i


@pytest.mark.live_ladder
def test_the_digest_ignores_only_the_location(tmp_path):
    c = next(iter(_generated(_copy(tmp_path / "ladder")).values()))
    moved = {**c, "ladder_pack": "/somewhere/else/set-1.json"}
    assert gen.content_digest(moved) == gen.content_digest(c)
    other = {**c, "generator": {**c["generator"], "key": str(c["generator"].get("key")) + "x"}}
    assert gen.content_digest(other) != gen.content_digest(c)       # content still counts


def _old_row(case, tree, **kw):
    from pathlib import Path
    stamp = version.location_bound_hash(
        {**case, "ladder_pack": str(Path(tree) / Path(case["ladder_pack"]).resolve().relative_to(ROOT_DIR.resolve()))})
    return {"case_id": case["id"], "bench_run_id": "r1", "repeat": 0, "case_revision": stamp,
            "ladder_revision": stamp, "score": 0.5, "grade": {"x": 1}, **kw}


@pytest.mark.live_ladder
def test_regrade_restamps_a_pack_row_made_under_another_checkout():
    from crucibleforge import regrade
    case = next(c for c in ladder.inline_cases() if gen.is_generated(c)
                and c["ladder_pack"].endswith("chat/character/set-1.json"))
    row = _old_row(case, "/var/tmp/old-tree")
    rec, = regrade.plan_pack_location([row], "/var/tmp/old-tree", "test")
    assert rec["status"] == "regrade"
    new, = rec["rows"]
    cur = version.case_hash(case["id"])
    assert new["case_revision"] == new["ladder_revision"] == cur
    assert new["score"] == 0.5 and new["grade"] == {"x": 1}                    # nothing regraded
    assert new["regraded_by"]["from_revision"] == row["case_revision"]
    rec2, = regrade.plan_pack_location([new], "/var/tmp/old-tree", "test")
    assert rec2["status"] == "current"                                        # once is enough


@pytest.mark.live_ladder
def test_regrade_refuses_a_pack_row_that_is_not_this_case_at_that_tree():
    from crucibleforge import regrade
    case = next(c for c in ladder.inline_cases() if gen.is_generated(c)
                and c["ladder_pack"].endswith("chat/character/set-1.json"))
    rec, = regrade.plan_pack_location([_old_row(case, "/var/tmp/old-tree")], "/var/tmp/another-tree", "t")
    assert rec["status"] == "refused" and "re-make it" in rec["why"]
    other = {**_old_row(case, "/var/tmp/old-tree"), "case_revision": "deadbeef", "ladder_revision": "deadbeef"}
    rec, = regrade.plan_pack_location([other], "/var/tmp/old-tree", "t")
    assert rec["status"] == "refused"
