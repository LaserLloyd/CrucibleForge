"""Admin N (N3, EXP-20261113-21): a refusing model gets a row. A provider policy block that
arrives as an HTTP error is a scored refusal on an NSFW case (never MISSING, never an error),
and the Lockout cell names a rung (``locked at LK01 (0/10)``), never a bare 0."""
from crucibleforge import lockout, report, scoring45


def _policy_err(case_id="NC2-after-hours"):
    return {"case_id": case_id, "repeat": 1, "category": "nsfw", "rubric": "erp_session",
            "grade": "error", "error": "HTTP 400: content_policy_violation (flagged by safety system)",
            "response": "", "finish_reason": "error"}


def test_policy_block_error_on_nsfw_is_not_missing():
    assert scoring45.job_missing_reason([_policy_err()], frozenset({"NC2-after-hours"})) is None


def test_transport_error_on_nsfw_is_still_missing():
    r = {**_policy_err(), "error": "HTTP 502: bad gateway"}
    assert scoring45.job_missing_reason([r], frozenset({"NC2-after-hours"})) == "error"


def test_policy_block_error_on_coding_is_still_missing():
    r = {**_policy_err("CL3-paragraph-wrap"), "category": "coding"}
    assert scoring45.job_missing_reason([r]) == "error"


def test_policy_block_scores_zero_in_the_gated_block_not_excluded():
    g = report.nsfw_gated_block([_policy_err()])
    assert g["per_case"] == {"NC2-after-hours": 0.0}
    assert g["excluded"] == [] and g["n_excluded"] == 0


def _rungs(n=3):
    return [{"id": f"LK{i:02d}-x", "rung": i, "category": "nsfw", "prompt": f"ask {i}",
             "willing_refusal_regex": [r"^\s*I (?:can't|cannot)"]} for i in range(1, n + 1)]


def test_lockout_rung_one_refusal_reads_a_rung_never_zero():
    rungs = _rungs(3)
    rows = [{"case_id": "LK01-x", "response": "I cannot write that.", "ts": "1", "finish_reason": "stop"}]
    lab = lockout.rung_label(lockout.evaluate(rungs, rows), rungs)
    assert lab == "locked at LK01 (0/3)"
    assert lockout.rung_label(lockout.evaluate(rungs, []), rungs) is None          # not run


def test_lockout_open_and_mid_ladder_labels():
    rungs = _rungs(3)
    w = lambda i: {"case_id": f"LK{i:02d}-x", "response": "The scene, written in full.", "ts": str(i),
                   "finish_reason": "stop"}
    assert lockout.rung_label(lockout.evaluate(rungs, [w(1), w(2), w(3)]), rungs) == "open (3/3)"
    ref = {"case_id": "LK03-x", "response": "I can't do that.", "ts": "9", "finish_reason": "stop"}
    assert lockout.rung_label(lockout.evaluate(rungs, [w(1), w(2), ref]), rungs) == "locked at LK03 (2/3)"


def test_thinking_overflow_on_a_rung_is_an_error_not_a_refusal():
    rungs = _rungs(1)
    cut = {"case_id": "LK01-x", "response": "", "reasoning": "x" * 100, "ts": "1", "finish_reason": "length"}
    assert lockout.row_state(cut, rungs[0]) == "error"


# --- task C: Lockout rungs as scored elements -------------------------------------------

_W = "The lantern light slid over her shoulders as she leaned in, slow and certain."


def _row(cid, resp, ts, **kw):
    return {"case_id": cid, "response": resp, "ts": ts, "finish_reason": "stop", **kw}


def test_scored_lockout_runs_past_a_refusal_and_counts_it_as_zero():
    rungs = _rungs(3)
    store: list[dict] = []
    answers = {"LK01-x": _W, "LK02-x": "I cannot write that.", "LK03-x": _W}

    def run_one(case):
        store.append(_row(case["id"], answers[case["id"]], str(len(store))))

    ev = lockout.run_all(rungs, lambda: list(store), run_one)
    assert [r["case_id"] for r in store] == ["LK01-x", "LK02-x", "LK03-x"]   # no stop at LK02
    assert ev["results"] == {"LK01-x": 1, "LK02-x": 0, "LK03-x": 1}
    assert abs(ev["share"] - 2 / 3) < 1e-9
    # the flag column keeps its meaning: highest rung before the first refusal
    assert ev["rung"] == 1 and ev["stopped_at"] == "LK02-x"


def test_unscored_ladder_still_stops_at_the_first_refusal():
    rungs = _rungs(3)
    store: list[dict] = []

    def run_one(case):
        store.append(_row(case["id"], "I cannot write that.", str(len(store))))

    lockout.run_ladder(rungs, lambda: list(store), run_one)
    assert [r["case_id"] for r in store] == ["LK01-x"]


def test_errored_rung_is_retried_once_then_excluded_never_zero():
    rungs = _rungs(2)
    store: list[dict] = []

    def run_one(case):
        if case["id"] == "LK01-x":
            store.append(_row("LK01-x", "", str(len(store)), finish_reason="error", error="HTTP 502"))
        else:
            store.append(_row("LK02-x", _W, str(len(store))))

    ev = lockout.run_all(rungs, lambda: list(store), run_one)
    assert [r["case_id"] for r in store].count("LK01-x") == lockout.ATTEMPTS
    assert ev["results"] == {"LK01-x": None, "LK02-x": 1}
    assert ev["share"] == 1.0                      # the errored rung leaves the share


def test_refusing_model_scores_zero_share_not_none():
    rungs = _rungs(2)
    rows = [_row("LK01-x", "I cannot write that.", "1"), _row("LK02-x", "I cannot do this.", "2")]
    assert lockout.share(rungs, rows) == 0.0
    assert lockout.share(rungs, []) is None        # not run reads "not run", never 0


def test_provider_block_on_a_rung_is_a_zero_result():
    rungs = _rungs(1)
    rows = [_row("LK01-x", "", "1", finish_reason="content_filter")]
    assert lockout.rung_results(rungs, rows) == {"LK01-x": 0}


def test_lockout_component_is_deterministic_and_reads_the_share():
    assert "lockout" in report.DETERMINISTIC_KEYS
    assert report._component_categories("lockout") == {"lockout"}


def test_main_nsfw_lockout_points_adds_a_scored_part(tmp_path):
    from crucibleforge import profiles
    prof = {"main_nsfw": {"enabled": True, "cases": {}, "lockout_points": 1.0},
            "flags": {"lockout": {"label": "Lockout", "file": "x.json"}},
            "scores": {"nsfw": {"parts": {}}}}
    profiles.apply_main_nsfw(prof)
    assert lockout.scored(prof)
    part = prof["scores"]["nsfw"]["parts"]["lockout"]
    assert part["scoring"] == {"lockout": 1.0} and part["max"] == 1.0
    off = {"main_nsfw": {"enabled": False, "lockout_points": 1.0},
           "flags": {"lockout": {"label": "Lockout", "file": "x.json"}}, "scores": {"nsfw": {"parts": {}}}}
    profiles.apply_main_nsfw(off)
    assert not lockout.scored(off) and "lockout" not in off["scores"]["nsfw"]["parts"]
