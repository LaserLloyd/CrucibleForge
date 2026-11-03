"""Part 3 of the conformance plan: the board notes and the report section
(2026-11-03).

The board has to say what the conformance checks found (a FAIL keeps the
model off the board, a WARN says what to look at), and it has to say when a
model's rows were measured under more than one request protocol -- those
rows cannot be compared with each other (R3).
"""
from __future__ import annotations

import json

import pytest

from crucibleforge import config, report


def _record(tmp_path, label="m", **kw):
    """A conformance record on disk."""
    d = tmp_path / "conformance"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"ts": "2026-11-03T09:00:00+00:00",
           "suite_revision": "4.0.1+abc",
           "protocol": {"provider_type": "openai", "reasoning_replay": True,
                        "extra_body_keys": ["reasoning_split"], "sampler": {}},
           "checks": [{"id": "c1", "status": "PASS", "detail": "served"},
                      {"id": "c3", "status": "PASS", "detail": "the chain holds"}],
           "verdict": "PASS"}
    rec.update(kw)
    (d / f"{label}.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


@pytest.fixture(autouse=True)
def _results(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    yield tmp_path


# ------------------------------------------------------------ the notes

def test_the_board_notes_name_the_failing_conformance_check(_results):
    _record(_results, checks=[{"id": "c1", "status": "FAIL", "detail": "404"},
                              {"id": "c3", "status": "PASS", "detail": ""}],
            verdict="FAIL")
    cov = report._coverage([], {"model_label": "m"}, None, label="m")
    assert "conformance FAIL: c1" in cov["notes"]


def test_the_board_notes_summarise_the_conformance_warnings(_results):
    _record(_results, checks=[{"id": "c1", "status": "PASS", "detail": ""},
                              {"id": "c6", "status": "WARN", "detail": "empty"}],
            verdict="WARN")
    cov = report._coverage([], {"model_label": "m"}, None, label="m")
    assert "conformance WARN: c6" in cov["notes"]
    assert not any(n.startswith("conformance FAIL") for n in cov["notes"])


def test_a_model_without_a_record_has_no_conformance_note(_results):
    cov = report._coverage([], {"model_label": "m"}, None, label="m")
    assert not any(n.startswith("conformance") for n in cov["notes"])


# --------------------------------------------------- the mixed protocols

def _rows(*protocols):
    return [{"case_id": "I1", "protocol": p} for p in protocols]


def test_rows_measured_under_two_protocols_say_they_predate_the_change():
    on = {"provider_type": "openai", "reasoning_replay": True,
          "extra_body_keys": ["reasoning_split"], "sampler": {}}
    off = dict(on, reasoning_replay=False)
    note = report._protocol_mix_note(_rows(on, off), {"provider_type": "openai"},
                                     None)
    assert note == "rows predate harness change reasoning_replay — re-run"


def test_the_mixed_protocol_note_names_every_field_that_moved():
    on = {"provider_type": "openai", "reasoning_replay": True,
          "extra_body_keys": ["reasoning_split"], "sampler": {}}
    off = {"provider_type": "openai", "reasoning_replay": False,
           "extra_body_keys": [], "sampler": {}}
    note = report._protocol_mix_note(_rows(on, off), {"provider_type": "openai"},
                                     None)
    assert "reasoning_replay" in note and "extra_body_keys" in note


def test_rows_under_one_protocol_are_not_called_stale():
    on = {"provider_type": "openai", "reasoning_replay": True,
          "extra_body_keys": [], "sampler": {}}
    assert report._protocol_mix_note(_rows(on, on), {"provider_type": "openai"},
                                     None) is None


def test_a_rig_model_does_not_get_the_mixed_protocol_note():
    """The plan scopes the note to API models."""
    on = {"provider_type": "studioforge", "reasoning_replay": False,
          "extra_body_keys": [], "sampler": {}}
    off = dict(on, reasoning_replay=True)
    assert report._protocol_mix_note(_rows(on, off),
                                     {"provider_type": "studioforge"},
                                     None) is None


def test_the_mixed_protocol_note_reaches_the_board_notes():
    on = {"provider_type": "openai", "reasoning_replay": True,
          "extra_body_keys": [], "sampler": {}}
    off = dict(on, reasoning_replay=False)
    rows = [{"case_id": "I1", "grade": "pass", "protocol": on},
            {"case_id": "I2", "grade": "pass", "protocol": off}]
    cov = report._coverage(rows, {"provider_type": "openai"}, None, label="m")
    assert any(n.startswith("rows predate harness change") for n in cov["notes"])


# ------------------------------------------------------- the report section

def test_report_md_lists_every_models_verdict_and_date(_results):
    _record(_results)
    lines = report._conformance_lines(["m"], {}, None)
    assert "## Conformance" in lines
    row = next(x for x in lines if x.startswith("| m |"))
    assert "PASS" in row and "2026-11-03" in row


def test_report_md_has_no_conformance_section_without_a_record(_results):
    assert report._conformance_lines(["m"], {}, None) == []


def test_the_rendered_report_carries_the_conformance_section(_results):
    _record(_results)
    (_results / "transcripts_m.jsonl").write_text(
        json.dumps({"category": "coding", "case_id": "C1", "repeat": 1,
                    "turn": None, "grade": "pass", "metrics": {}}) + "\n",
        encoding="utf-8")
    (_results / "meta_m.json").write_text(
        json.dumps({"device": "fake", "failed": False}), encoding="utf-8")
    stats = report.model_stats("m")
    md = report.render_markdown(["m"], {"m": stats}, None)
    assert "## Conformance" in md
    assert "| m | PASS |" in md
