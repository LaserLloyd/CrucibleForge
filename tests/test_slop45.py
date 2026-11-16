"""Main NSFW slop metric: report-only, deterministic, versioned list, never in a prompt or a score."""
import hashlib
import json
import pathlib

from crucibleforge import report, slop45

PLAIN = "She set the cup down and looked at the window. He said nothing for a while. " * 120


def _row(text, refused=False, case="MN1-x", finish="stop"):
    return {"case_id": case, "response": text, "judge": {"refused": refused}, "finish_reason": finish}


def _slop_text():
    sw = sorted(slop45.lists()[0])[:60]
    return (" ".join(sw) + " ") * 30


def test_deterministic():
    rows = [_row(PLAIN), _row(_slop_text())]
    assert slop45.score_rows(rows) == slop45.score_rows(list(rows))


def test_stuffed_scores_high_slop_low_credit():
    s = slop45.score_rows([_row(_slop_text())])
    assert s["slop_words_1k"] > 500 and s["credit"] == 0.0 and s["gated"] is None


def test_plain_text_low_slop_full_credit():
    s = slop45.score_rows([_row(PLAIN)])
    assert s["words"] >= slop45.MIN_WORDS and s["slop_words_1k"] < 2 and s["credit"] == 1.0


def test_credit_scale():
    assert slop45.credit(2.0) == 1.0 and slop45.credit(15.0) == 0.0
    assert abs(slop45.credit(8.5) - 0.5) < 1e-9


def test_refused_rows_excluded():
    s = slop45.score_rows([_row(PLAIN), _row(_slop_text(), refused=True),
                           _row(_slop_text(), finish="content_filter")])
    assert s["n_rows"] == 1 and s["slop_words_1k"] < 2


def test_gate_under_1500_words():
    s = slop45.score_rows([_row("a short reply. " * 20)])
    assert s["credit"] is None and s["gated"] == "too_little_text"


def test_list_not_in_any_case_prompt():
    """No case file (prompts) carries a slop trigram, and no module other than slop45 loads the 4.5.0 lists."""
    root = pathlib.Path(slop45.__file__).parent
    tri = [t for t in slop45.lists()[1] if len(t) > 14]
    for p in list(root.rglob("*.yaml")) + list((root.parent / "cases").rglob("*.yaml")):
        if "data/slop" in p.as_posix():
            continue
        t = p.read_text(encoding="utf-8", errors="ignore").lower()
        # ordinary prose may share a phrase or two; a pasted list would share dozens
        assert len([x for x in tri if x in t]) < 8, p
    for p in root.glob("*.py"):
        if p.name != "slop45.py":
            assert "slop/4.5.0" not in p.read_text(encoding="utf-8", errors="ignore"), p.name


def test_manifest_sha256_matches():
    m = json.loads((slop45.DATA / "manifest.json").read_text(encoding="utf-8"))
    assert m["release"] == slop45.RELEASE == (slop45.DATA / "VERSION").read_text(encoding="utf-8").strip()
    for name, h in m["sha256"].items():
        assert hashlib.sha256((slop45.DATA / name).read_bytes()).hexdigest() == h, name


def test_main_nsfw_rows_selection():
    rows = [_row("x", case="MN1-a"), _row("y", case="OTHER")]
    prof = {"main_nsfw": {"enabled": True, "cases": {"mn1": "MN1-a"}}}
    assert [r["case_id"] for r in slop45.main_nsfw_rows(rows, prof)] == ["MN1-a"]
    assert len(slop45.main_nsfw_rows(rows, {"main_nsfw": {"enabled": False}})) == 2


def test_report_carries_slop_but_scores_ignore_it():
    import inspect
    src = inspect.getsource(report)
    assert '"slop": _slop' in src
    # no scoring / points / tier code reads the block
    for line in src.splitlines():
        if "slop" in line and "def " not in line:
            assert not any(k in line for k in ("points", "tier", "weight")) or "report-only" in line or "_slop" in line
    rows = [{**_row(PLAIN, case="NC1-tideglass"), "category": "nsfw", "rubric": "erp_session",
             "needs_judge": True, "judge": {"refused": False, "scores": {"erotic": 6, "explicitness": 6}}}]
    a = report.nsfw_gated_block(rows)
    b = report.nsfw_gated_block([{**r, "response": _slop_text()} for r in rows])
    assert a == b
