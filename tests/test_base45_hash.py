"""A5: the five Base cases keep what the model SEES byte-identical to 4.1.

Grading-only edits (CC4 W2 / J1 / J3 and the standard-45 elements, NC2 `limit-*` and
elements, WL2 programmatic W) may move the case hash and the grading signature, never the
generation view; a stored 4.1 row then stays reusable and is marked "re-judge required"
(A0b, `report.reuse_status`).
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASE = ["CL3-paragraph-wrap", "TZ16-pickup-dst-delay", "CC4-second-pitch", "WL2-lighthouse",
        "NC2-after-hours"]
#: no grading edit at all: the whole hash is unchanged
UNTOUCHED = ["CL3-paragraph-wrap", "TZ16-pickup-dst-delay"]
#: grading-only edits: generation view equal, re-judge required
EDITED = ["CC4-second-pitch", "WL2-lighthouse", "NC2-after-hours"]
ARCH = ROOT / "archive" / "4.1.0" / "cases"


@pytest.fixture(scope="module")
def trees():
    from crucibleforge import version
    arch = version.tree_views(ARCH)
    live = version.tree_views(ROOT / "cases")
    return arch, live


@pytest.mark.parametrize("cid", BASE)
def test_generation_view_equal_under_bench_and_bench45(trees, cid):
    from crucibleforge import version
    (_ta, ra), (_tl, rl) = trees
    assert version.generation_view(rl[cid]) == version.generation_view(ra[cid]), cid


@pytest.mark.parametrize("cid", UNTOUCHED)
def test_untouched_base_case_hash_and_grading_equal(trees, cid):
    (ta, _ra), (tl, _rl) = trees
    assert tl[cid] == ta[cid], cid


def test_wl2_keeps_its_case_hash(trees):
    """WL2's edit (programmatic W: `judge_willingness` off) is a 4.1 grading key, so its case hash and
    its stored rows' currency are untouched; only the grading hash and signature move."""
    (ta, _ra), (tl, _rl) = trees
    assert tl["WL2-lighthouse"]["case"] == ta["WL2-lighthouse"]["case"]
    assert tl["WL2-lighthouse"]["grading"] != ta["WL2-lighthouse"]["grading"]


@pytest.mark.parametrize("cid", ["CC4-second-pitch", "NC2-after-hours"])
def test_standard_45_stamp_moves_the_live_hash_but_not_what_the_model_sees(trees, cid):
    """The documented consequence: `element_standard: 45` also skips the 4.5 grading keys in the hash,
    so the live hash differs from the stored stamp; reuse_status (generation view) is the gate."""
    from crucibleforge import version
    (ta, ra), (tl, rl) = trees
    assert tl[cid]["case"] != ta[cid]["case"]
    assert version.generation_view(rl[cid]) == version.generation_view(ra[cid])


@pytest.mark.parametrize("cid", EDITED)
def test_edited_base_case_changes_only_its_grading_signature(trees, cid):
    from crucibleforge import version
    (_ta, ra), (_tl, rl) = trees
    assert version.grading_signature(rl[cid]) != version.grading_signature(ra[cid]), cid


def _stored():
    return json.loads((ROOT / "tests" / "fixtures" / "stored_base_rows.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("cid", BASE)
def test_stored_row_stamps_are_the_frozen_tree_hash(trees, cid):
    """The scrubbed real stored rows carry the 4.1 case stamp, i.e. the archive tree's hash."""
    (ta, _ra), _ = trees
    stamps = {r["case_revision"] for r in [_stored()[cid]]}
    assert stamps == {ta[cid]["case"]}, (cid, stamps, ta[cid]["case"])


@pytest.mark.parametrize("cid", BASE)
def test_stored_rows_reusable_and_rejudge_flag_follows_grading(cid):
    from crucibleforge import config, profiles, report
    prof = profiles.load_profile("bench45", config.load_config(config.EXAMPLE_CONFIG_PATH))
    prof = {**prof, "reuse": {"from": ["bench"], "ids": BASE}}
    row = dict(_stored()[cid])
    from crucibleforge import version
    arch = version.tree_views(ARCH)[0]
    row["case_revision"] = arch[cid]["case"]
    row.setdefault("profile", "bench")
    ok, rejudge = report.reuse_status(row, prof)
    assert ok is True, cid
    assert rejudge is (cid in EDITED), (cid, rejudge)


def test_characters_is_author_side_only():
    """version._GENERATION_SKIP_EXTRA += characters (A5, S10 check): the EL-17 age declaration
    is never sent to a model, so it is out of the generation view and in the grading signature.
    Guard: only the element lint (and the generators that write it) read the key."""
    import re
    from crucibleforge import version
    c = {"id": "X", "prompt": "p", "characters": [{"name": "Noor", "age": 31}]}
    assert "characters" not in version.generation_view(c)
    assert version.generation_view(c) == version.generation_view({k: v for k, v in c.items()
                                                                  if k != "characters"})
    assert version.grading_signature(c) != version.grading_signature({"id": "X", "prompt": "p"})
    pkg = Path(version.__file__).resolve().parent
    readers = sorted(str(p.relative_to(pkg)) for p in pkg.rglob("*.py")
                     if re.search(r"""(get\(|\[)["']characters["']""", p.read_text(encoding="utf-8")))
    assert all(r == "element_lint.py" or r.startswith("gen/") for r in readers), readers
