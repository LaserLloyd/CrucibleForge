"""A0b: stored-row reuse compares the row's stamp with the SOURCE tree and the
generation view; a grading-only edit keeps the answer and marks it re-judge."""
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CL7 = "CL7-span-by-example"
CC4 = "CC4-second-pitch"
NC2 = "NC2-after-hours"


@pytest.fixture
def tree(monkeypatch, tmp_path):
    """A writable copy of the live cases/ as the active tree; returns (edit, prof)."""
    from crucibleforge import config, profiles, report, version
    dst = tmp_path / "cases"
    shutil.copytree(ROOT / "cases", dst)
    monkeypatch.setattr(profiles, "DEFAULT_PROFILE", "bench45")
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    prof = profiles.load_profile("bench45", cfg)
    prof = {**prof, "reuse": {"from": ["bench"], "ids": [CL7, CC4, NC2]}}

    def activate():
        config.set_cases_root(dst)
        version._CASE_TABLE.clear()
        report._CASES_BY_ID = None

    def edit(cid, fn):
        for p in dst.glob("*.json"):
            data = json.loads(p.read_text(encoding="utf-8"))
            cases = data if isinstance(data, list) else data["cases"]
            hit = [c for c in cases if c.get("id") == cid]
            if hit:
                fn(hit[0])
                p.write_text(json.dumps(data, indent=1), encoding="utf-8")
                activate()
                return
        raise AssertionError(cid)

    activate()
    yield edit, prof
    config.set_cases_root(ROOT / "cases")
    version._CASE_TABLE.clear()
    report._CASES_BY_ID = None


def _row(cid):
    """A stored bench row as 4.1 wrote it: stamp = the frozen tree's hash."""
    from crucibleforge import version
    table = version.case_table(ROOT / "archive/4.1.0/cases", ladder_dirs=[ROOT / "archive/4.1.0/cases/ladder"])
    return {"profile": "bench", "case_id": cid, "category": "x", "repeat": 1, "bench_run_id": "b1",
            "bench_revision": "4.1.0+deadbeef", "ts": "2026-11-01T00:00:00",
            "case_revision": table[cid]["case"], "regime": "deployed",
            "finish_reason": "stop", "metrics": {"completion_tokens": 10}}


def _status(row, prof):
    from crucibleforge import report
    return report.reuse_status(row, prof)


@pytest.mark.parametrize("cid", [CC4, NC2])
def test_the_a5_grading_edits_keep_the_row_and_mark_rejudge(tree, cid):
    """CC4 / NC2 carry A5's grading-only edits live (standard-45 elements, unscored checks):
    the 4.1 row stays reusable and is marked "re-judge required"."""
    _edit, prof = tree
    assert _status(_row(cid), prof) == (True, True)


@pytest.mark.parametrize("cid", [CC4, NC2])
def test_unchanged_grading_is_reusable_without_rejudge(tree, cid):
    """Revert the grading to the frozen text in a copy: nothing to re-judge."""
    import json as _json
    edit, prof = tree
    arch = {c["id"]: c for p in ("rpdeep", "nsfw")
            for c in _json.loads((ROOT / "archive/4.1.0/cases" / f"{p}.json").read_text(encoding="utf-8"))}

    def revert(c):
        c.clear()
        c.update(arch[cid])
    edit(cid, revert)
    assert _status(_row(cid), prof) == (True, False)


def test_cl7_gets_its_rows_back(tree):
    """CL7 already carries element_standard 45 live: its hash moved, its prompt did not."""
    from crucibleforge import version
    _edit, prof = tree
    arch = version.case_table(ROOT / "archive/4.1.0/cases", ladder_dirs=[ROOT / "archive/4.1.0/cases/ladder"])
    assert version.case_hash(CL7) != arch[CL7]["case"]
    assert _status(_row(CL7), prof)[0] is True


@pytest.mark.parametrize("cid", [CL7, CC4, NC2])
def test_element_standard_45_rewrite_keeps_the_row_and_marks_rejudge(tree, cid):
    from crucibleforge import report, version
    edit, prof = tree
    before = version.case_hash(cid)
    from crucibleforge import version as _v
    arch = _v.case_table(ROOT / "archive/4.1.0/cases", ladder_dirs=[ROOT / "archive/4.1.0/cases/ladder"])

    def rewrite(c):
        c["element_standard"] = 45
        for e in c.get("judge_elements") or []:
            e["fact"] = (e.get("fact") or "") + " (45)"
        c["rubric"] = "changed-rubric"
    edit(cid, rewrite)
    assert version.case_hash(cid) != arch[cid]["case"]   # the live hash moved: the old bug
    row = _row(cid)
    assert report.reusable_row(row, prof) is True
    assert _status(row, prof) == (True, True)
    assert report.rejudge_required(row, prof) is True


@pytest.mark.parametrize("cid", [CC4, NC2])
def test_a_changed_prompt_is_not_reusable(tree, cid):
    edit, prof = tree

    def reword(c):
        c["element_standard"] = 45
        if c.get("turns"):
            t = c["turns"][0]
            if isinstance(t, dict):
                t["content"] = t.get("content", "") + " Also be brief."
            else:
                c["turns"][0] = t + " Also be brief."
        else:
            c["prompt"] = (c.get("prompt") or "") + " Also be brief."
    edit(cid, reword)
    assert _status(_row(cid), prof) == (False, False)


def test_a_changed_system_prompt_is_not_reusable(tree):
    edit, prof = tree
    edit(CL7, lambda c: c.__setitem__("system", (c.get("system") or "") + " X"))
    assert _status(_row(CL7), prof) == (False, False)


def test_a_row_stamped_for_other_content_is_not_reusable(tree):
    _edit, prof = tree
    row = {**_row(CC4), "case_revision": "00000000"}
    assert _status(row, prof) == (False, False)


def test_own_profile_rows_and_unlisted_ids_unchanged(tree):
    _edit, prof = tree
    assert _status({"profile": "bench45", "case_id": CC4}, prof) == (True, False)
    assert _status({**_row("CL3-paragraph-wrap")}, prof) == (False, False)
