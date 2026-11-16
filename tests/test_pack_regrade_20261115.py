"""Pack-question rows regrade after a grading-only change (lead 01:05, 2026-11-15).

After T's IMPLIED grading change the PK-C1-IMPLIED-L5-01..08 rows in the rc2 board folders read
"pending, incomplete": their stored case_revision is no longer the case hash, and ``regrade`` only
grouped chain rows (a pack row has no chain block), so no supported path brought them back.
``regrade.plan_pack`` (run in the same pass as the chain regrade, under any composition) rebuilds the
question, refuses when what was sent differs, and otherwise grades the stored reply again with the
live run's code and stamps the current hash. The pack stamp hashes the questions, not the set file's
free text: a note-only edit moves nothing."""
from __future__ import annotations

import json

import pytest

from crucibleforge import config, ladder, regrade, runner, version

pytestmark = pytest.mark.live_ladder
CID = "PK-C1-IMPLIED-L5-01"
SET = "work/c1_implied/set-1.json"


@pytest.fixture(scope="module")
def case():
    raw = {c["id"]: c for c in ladder.inline_cases()}[CID]
    c = json.loads(json.dumps(raw))
    config._materialize_generated(c)
    return c


def _row(case, response="no idea", stamp="00000000", **over):
    r = {"bench_run_id": "run-a", "case_id": CID, "category": case["category"], "repeat": 1, "turn": None,
         "profile": "bench45", "prompt": case.get("prompt"), "system": case.get("system"),
         "response": response, "finish_reason": "stop", "case_revision": stamp,
         "grade": "fail", "score": 0.0, "metrics": {"completion_tokens": 3}}
    r.update(over)
    return r


def test_a_stale_pack_row_sent_identically_is_regraded_and_stamped_current(case):
    row = _row(case)
    recs = regrade.plan_pack([row], "T's IMPLIED grading", cases=None)
    assert [r["status"] for r in recs] == ["regrade"], recs
    new = recs[0]["rows"][0]
    assert new["case_revision"] == version.case_hash(CID) and version.row_case_stamp(new) == version.case_hash(CID)
    # the grade is exactly the live run's grade of the same reply
    want = json.loads(json.dumps(row))
    for k in regrade.ROW_GRADE_KEYS:
        want.pop(k, None)
    runner.grade_single_row(want, regrade._result_of(row), case, False, None)
    assert new.get("grade") == want.get("grade") and new.get("score") == want.get("score")
    assert new.get("elements") == want.get("elements")
    assert new["regraded_by"]["pack"] is True and new["regraded_by"]["from_revision"] == "00000000"
    assert row["case_revision"] == "00000000"                       # the stored row is not touched
    # idempotent: the superseding row is current
    assert [r["status"] for r in regrade.plan_pack([new], "again")] == ["current"]


def test_a_pack_row_whose_prompt_differs_is_refused(case):
    recs = regrade.plan_pack([_row(case, prompt=(case.get("prompt") or "") + " ")], "x")
    assert recs[0]["status"] == "refused" and "what was sent differs" in recs[0]["why"]


def test_an_errored_pack_row_is_refused_and_part_filters(case):
    assert regrade.plan_pack([_row(case, error="timeout")], "x")[0]["status"] == "refused"
    assert regrade.plan_pack([_row(case)], "x", part="work/c1_implied")[0]["status"] == "regrade"
    assert regrade.plan_pack([_row(case)], "x", part="work/other") == []


def test_the_cli_regrades_pack_rows_under_a_composition(case, tmp_path, monkeypatch):
    """One supported path: ``python -m crucibleforge.regrade MODEL --reason R --go`` with
    CF_COMPOSITION=rc3-main (under which every chain row of a pack folder is refused) brings the pack
    row back current."""
    from crucibleforge import profiles
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc3-main")
    monkeypatch.setenv("CRUCIBLEFORGE_CONFIG", str(config.EXAMPLE_CONFIG_PATH))
    (tmp_path / "transcripts_m.jsonl").write_text(json.dumps(_row(case)) + "\n", encoding="utf-8")
    assert regrade.main(["m", "--reason", "T's IMPLIED grading", "--go"]) == 0
    rows = config.load_transcripts("m")
    assert len(rows) == 1 and rows[0]["case_revision"] == version.case_hash(CID)
    assert regrade.main(["m", "--reason", "again", "--go"]) == 0
    assert len((tmp_path / "transcripts_m.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_a_note_only_edit_moves_no_pack_stamp(tmp_path):
    """The pack stamp hashes what is sent and graded: the set file's ``note`` (free text) is not in it,
    and an edit to a question does move it (the guard can see a change)."""
    src = ladder.LADDER_DIR / SET
    empty = tmp_path / "cases"
    empty.mkdir()

    def table(text):
        d = tmp_path / f"l{len(list(tmp_path.iterdir()))}"
        (d / SET).parent.mkdir(parents=True)
        (d / SET).write_text(text, encoding="utf-8")
        return version.case_table(cases_dir=empty, ladder_dirs=[d])

    data = json.loads(src.read_text(encoding="utf-8"))
    base = table(json.dumps(data))
    ids = [c["id"] for c in data["cases"]]
    assert set(ids) <= set(base) and base[CID]["case"] == version.case_hash(CID)
    noted = table(json.dumps({**data, "note": data["note"] + " (edited)"}))
    assert {i: noted[i] for i in ids} == {i: base[i] for i in ids}
    moved = json.loads(json.dumps(data))
    moved["cases"][0]["generator"]["deal"]["slot"] = 1
    assert table(json.dumps(moved))[CID]["case"] != base[CID]["case"]
