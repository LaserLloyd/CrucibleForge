"""4.1 invariance (review F6): every case that existed at base 4fb921c keeps
its case and grading hash, so no stored 4.1 verdict is staled by 4.5 code."""
import pytest
import json
from pathlib import Path

from crucibleforge import version

GOLDEN = Path(__file__).parent / "fixtures" / "bench41_case_hashes_4fb921c.json"


@pytest.mark.usefixtures("cases_41")
def test_every_bench_case_hash_equals_base():
    base = json.loads(GOLDEN.read_text(encoding="utf-8"))
    table = version.case_table()
    moved = {k: (v, table.get(k)) for k, v in base.items() if table.get(k) != v}
    assert not moved, moved


def test_stamped_nc2_row_needs_no_regrade():
    """4.1 side: the frozen tree still hashes NC2 grading to the stamp its rows carry. Live (4.5) NC2 had its
    elements rewritten to standard 45 (A5, 2026-11-12): a 4.1-stamped verdict IS re-judged there."""
    arch = version.case_table(Path(__file__).resolve().parent.parent / "archive" / "4.1.0" / "cases")
    assert arch["NC2-after-hours"]["grading"] == "0b450d43"
    assert version.grading_hash("NC2-after-hours") != "0b450d43"
    row = {"case_id": "NC2-after-hours", "judge": {"grading_revision": "0b450d43"}}
    assert version.regrade_needed(row) is True


def test_45_only_element_keys_are_score_only_for_standard_45_cases():
    els = lambda extra: [{"id": "a", "text": "x", "weight": 1, **extra}]
    old = {"judge_elements": els({"short_quote": True})}
    new = dict(old, element_standard=45)
    plain = {"judge_elements": els({})}
    assert version._h8(version._grading_view(old)) != version._h8(version._grading_view(plain))
    assert version._h8(version._grading_view(new)) == version._h8(version._grading_view(
        dict(plain, element_standard=45)))


def test_element_standard_key_moves_no_whole_file_hash(tmp_path, monkeypatch):
    """Adding `element_standard: 45` to a case file is structure, not content."""
    import shutil
    from crucibleforge import config
    d = tmp_path / "cases"
    d.mkdir()
    for f in ("coding.json", "codeagent.json"):
        shutil.copy(config.CASES_DIR / f, d / f)
    monkeypatch.setattr(version, "_CASES_DIR", d)
    before = (version.cases_hash(), version.bank_category_hash("codeagent"))
    for f in ("coding.json", "codeagent.json"):
        data = json.loads((d / f).read_text(encoding="utf-8"))
        for c in (data if isinstance(data, list) else data["cases"]):
            c["element_standard"] = 45
        (d / f).write_text(json.dumps(data), encoding="utf-8")
    assert (version.cases_hash(), version.bank_category_hash("codeagent")) == before
