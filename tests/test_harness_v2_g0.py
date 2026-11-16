"""Harness v2, gate G0 (spec v2 §7.2): a dummy private string never reaches
the public results, git or DisPatch — checked on the v2 surfaces (W/C per-case
grid, filter / same-family marks, staged rows, lint notes).

The fixtures are the existing throwaway private layers of
test_private_categories (results side) and test_private_layer (git side)."""
from __future__ import annotations

import pytest

import json
import re
from pathlib import Path

from crucibleforge import report
from test_private_categories import IDS, LABEL, CAT, _write_rows, private_env  # noqa: F401
from test_private_layer import IDS as LAYER_IDS, scrub  # noqa: F401

CANARY = "ZZ-PRIVATE-CANARY-4417"
PKG = Path(__file__).resolve().parent.parent / "crucibleforge"


def _poison(env):
    """Give the private rows everything the v2 scoring reads (W rung,
    elements, checks, a filter mark, a same-family judge) and the canary in
    every free-text field."""
    f = next(env["results"].glob("transcripts_*.jsonl"))
    rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    for r in rows:
        if r.get("case_id") in IDS:
            r.update({"willing": "must_write", "judge_willingness": True,
                      "response": f"{CANARY} story text", "model_id": "x/y",
                      "judge_model": "x/y", "finish_reason": "content_filter",
                      "checks": {"results": [{"id": f"{CANARY}-chk", "pass": False,
                                              "detail": CANARY}]}})
            r["judge"].update({"willingness": {"w": 4, "why": CANARY},
                               "elements": {"results": [{"id": f"{CANARY}-el", "pass": True,
                                                         "credit": 1.0, "quote": CANARY}]}})
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_g0_public_artifacts_carry_no_private_string(private_env):
    _write_rows(private_env)
    _poison(private_env)
    report.generate(write=True, public=True)
    pub = private_env["results"] / "public"
    texts = {p.name: p.read_text(encoding="utf-8") for p in pub.iterdir()}
    assert texts
    for name, text in texts.items():
        for term in (CANARY, CAT, LABEL, *IDS):
            assert term.lower() not in text.lower(), (name, term)


@pytest.mark.archive_default
def test_g0_wc_grid_of_a_public_board_skips_private_rows(private_env):
    _write_rows(private_env)
    _poison(private_env)
    # control: the local board does read the poisoned private rows
    _, local, _ = report.board_stats(None, private_env["cfg"])
    assert any(t in json.dumps(local, default=str) for t in (CANARY, *IDS))
    cfg = {**private_env["cfg"], "_audience": report.PUBLIC}
    labels, stats, _ = report.board_stats(None, cfg)
    blob = json.dumps(stats, default=str)
    for term in (CANARY, *IDS):
        assert term not in blob


def test_g0_git_scrub_refuses_a_private_case_id(scrub):  # noqa: F811
    msg = f"WP0 notes: case {LAYER_IDS[0]} scored W4"
    problems = scrub.scan_text(msg, "msg")
    assert problems and LAYER_IDS[0] not in "\n".join(problems)


def test_g0_crucibleforge_has_no_dispatch_path():
    """CrucibleForge never posts to DisPatch (the operator does, from the
    public board only): no module names its inject/upload API or port."""
    pat = re.compile(r"api/inject|api/upload|dispatch-api|:8765")
    hits = [p.name for p in PKG.rglob("*.py") if pat.search(p.read_text(encoding="utf-8"))]
    assert hits == []
