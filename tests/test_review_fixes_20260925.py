"""Reviewer findings on the 3.5.0 differentiation round (2026-09-25)."""
import argparse

import pytest

from crucibleforge import cli


# ------------------------------------------------ B2: --fresh with a filter

def _run_args(**kw):
    base = dict(cmd="run", models="m", categories=None, difficulty=None, cases=None,
                fresh=False, force=False, smoke=False, yes=True, profile=None, judge=None)
    base.update(kw)
    return argparse.Namespace(**base)


class _Stop(Exception):
    pass


def _stub_run(monkeypatch, archived):
    cases = [{"id": "A1", "category": "math"}, {"id": "B1", "category": "rp"}]
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda a, c: (c, list(cases), None))
    monkeypatch.setattr(cli, "resolve_models", lambda cfg, m: [{"name": "m"}])
    monkeypatch.setattr(cli, "_archive_labels", lambda labels: archived.extend(labels))

    def stop(*a, **k):
        raise _Stop
    monkeypatch.setattr(cli, "_fail_fast", stop)


@pytest.mark.parametrize("flt", [{"cases": "A1"}, {"categories": "rp"}])
def test_fresh_with_a_filter_is_refused(monkeypatch, flt):
    archived = []
    _stub_run(monkeypatch, archived)
    with pytest.raises(SystemExit) as ei:
        cli.cmd_run(_run_args(fresh=True, **flt), {})
    msg = str(ei.value)
    assert "--fresh" in msg and "WHOLE" in msg and "--force" in msg
    assert archived == []


def test_fresh_with_a_filter_and_force_archives(monkeypatch):
    archived = []
    _stub_run(monkeypatch, archived)
    with pytest.raises(_Stop):
        cli.cmd_run(_run_args(fresh=True, force=True, cases="A1"), {})
    assert archived == ["m"]


def test_fresh_alone_and_filter_alone_still_work(monkeypatch):
    archived = []
    _stub_run(monkeypatch, archived)
    with pytest.raises(_Stop):
        cli.cmd_run(_run_args(fresh=True), {})
    assert archived == ["m"]
    archived.clear()
    with pytest.raises(_Stop):
        cli.cmd_run(_run_args(cases="A1"), {})
    assert archived == []


def test_run_parser_takes_force_and_it_is_not_force_evict(monkeypatch, tmp_path):
    """`run` had no --force, so argparse's prefix matching silently turned
    `--force` into `--force-evict` (a rig-eviction flag)."""
    from crucibleforge import graders
    seen = {}
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: {"defaults": {}})
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", tmp_path / "v2")
    monkeypatch.setattr(cli, "acquire_rig_lock", lambda: None)
    monkeypatch.setattr(graders, "require_sandbox", lambda: None)
    monkeypatch.setattr(cli, "cmd_run", lambda args, cfg: seen.setdefault("a", args) and 0)
    with pytest.raises(SystemExit):
        cli.main(["run", "--models", "m", "--fresh", "--cases", "A1", "--force"])
    assert seen["a"].force is True and seen["a"].force_evict is False


# --------------------------------------- S1: steer follows the RECHECKED checks

def _steer_row(scores, checks_pass=True, elements_pass=True):
    return {"model_label": "m", "category": "steer", "case_id": "SX1-harborline-ledger",
            "rubric": "steer", "needs_judge": True, "response": "r",
            "checks": {"results": [{"id": "c1", "group": "constraint", "pass": checks_pass,
                                    "detail": ""}]},
            "judge": {"judge_failed": False, "scores": scores,
                      "elements": {"results": [{"id": "e1", "pass": elements_pass}],
                                   "rate": 1.0 if elements_pass else 0.0}}}


def test_steer_rate_is_judge_verdict_and_rechecked_checks():
    from crucibleforge import report
    # judged obeyed, a (since fixed) check folded it to False at judge time;
    # the recheck now passes -> the board counts it obeyed without judge --force
    fixed = _steer_row({"obeyed": False, "obeyed_judge": True,
                        "obeyed_failed_checks": ["c1"]}, checks_pass=True)
    assert report.steer_obeyed(fixed) is True
    # a check that still fails on the recheck keeps it NOT obeyed
    assert report.steer_obeyed(_steer_row({"obeyed": True}, checks_pass=False)) is False
    # a failed judge element (quote guard) keeps it NOT obeyed
    assert report.steer_obeyed(_steer_row({"obeyed": False, "obeyed_judge": True},
                                          elements_pass=False)) is False
    # the judge itself said no
    assert report.steer_obeyed(_steer_row({"obeyed": False})) is False
    # old single-turn rows: no checks, no elements -> the judge verdict
    plain = {"judge": {"scores": {"obeyed": True}}}
    assert report.steer_obeyed(plain) is True
    s = report.model_stats("m", rows=[fixed])
    assert s["steer"]["rate"] == 1.0


# ---------------------------------------------------- S3: forbid_args on calls

def _call(name, **args):
    import json
    return [{"name": name, "arguments": json.dumps(args)}]


def test_grade_tool_call_forbid_args():
    from crucibleforge import graders
    cfg = {"expect_tool": "create_meeting",
           "required_args": {"attendee_ids": ["e-3308"]},
           "forbid_args": {"attendee_ids": ["e-3380", "e-5102"]}}
    ok = graders.grade_tool_call(_call("create_meeting", attendee_ids=["E-3308", "E-5120"]), "", cfg)
    assert ok["grade"] == "pass"
    bad = graders.grade_tool_call(_call("create_meeting", attendee_ids=["E-3308", "E-5102"]), "", cfg)
    assert bad["grade"] == "fail" and "e-5102" in bad["detail"].lower()
    # an absent forbidden arg is not a failure
    cfg2 = {"expect_tool": "x", "forbid_args": {"other": ["zzz"]}}
    assert graders.grade_tool_call(_call("x", a=1), "", cfg2)["grade"] == "pass"


def test_tool_loop_passes_forbid_args(monkeypatch):
    from crucibleforge import runner
    from crucibleforge.api import ChatResult
    case = {"id": "T", "category": "tooluse",
            "tools": [{"type": "function", "function": {"name": "create_meeting",
                                                         "parameters": {"type": "object"}}}],
            "tool_script": [{"user": "go", "expect_tool": "create_meeting",
                             "required_args": {"attendee_ids": ["e-3308"]},
                             "forbid_args": {"attendee_ids": ["e-5102"]},
                             "tool_result": "{}"}]}

    class Ctx:
        def call(self, messages, **kw):
            return ChatResult(response_text="", finish_reason="tool_calls",
                              tool_calls=[{"id": "c1", "name": "create_meeting",
                                           "arguments": '{"attendee_ids": ["E-3308", "E-5102"]}'}])

        def recover_for(self, case):
            return False
    row = runner._run_tool_loop(case, {}, Ctx(), 100, 0.0, 1)
    assert row["grade"] == "fail" and "e-5102" in row["grade_detail"].lower()


def test_tz13_forbids_the_lookalike_ids_and_verify_checks_the_key():
    from crucibleforge import verify_cases
    from crucibleforge.config import load_cases
    c = next(c for c in load_cases(["tooluse"]) if c["id"] == "TZ13-meeting-dependent-args")
    for k in (2, 4):  # steps 3 and 5
        fa = c["tool_script"][k]["forbid_args"]["attendee_ids"]
        assert {"e-5102", "e-3380"} <= {x.lower() for x in fa}
    assert verify_cases._check(c) == []
    bad = {**c, "tool_script": [dict(s) for s in c["tool_script"]]}
    bad["tool_script"][2]["forbid_args"] = {"attendee_ids": "e-5102"}      # not a list
    assert any("forbid_args" in e for e in verify_cases._check(bad))
    bad["tool_script"][2]["forbid_args"] = ["e-5102"]                      # not a mapping
    assert any("forbid_args" in e for e in verify_cases._check(bad))


def test_tz11_step10_forbids_the_old_dob_and_tz12_step10_is_word_bounded():
    from crucibleforge import graders
    from crucibleforge.config import load_cases
    by = {c["id"]: c for c in load_cases(["tooluse"])}
    s10 = by["TZ11-clinic-ask-then-correct"]["tool_script"][9]
    assert any("1988" in f for f in s10["answer_forbid"])
    s10 = by["TZ12-stock-transfer-pallets"]["tool_script"][9]
    cfg = {"needles": s10["answer_contains"], "match": "all"}
    assert graders.grade_contains("AVAILABLE 42 LEFT 2", cfg)["grade"] == "pass"
    assert graders.grade_contains("AVAILABLE 42 LEFT 22", cfg)["grade"] == "fail"
    assert graders.grade_contains("AVAILABLE 420 LEFT 2", cfg)["grade"] == "fail"


def test_regex_needles():
    from crucibleforge import graders
    assert graders._needle_hit("re:\\bleft 2\\b", "left 2.")
    assert not graders._needle_hit("re:\\bleft 2\\b", "left 25")
    assert graders._needle_hit(["nope", "re:\\byes\\b"], "yes")


# ------------------------------------------ S4: fingerprint = the PROFILE judge

def test_judge_fingerprint_hashes_the_profile_judge(tmp_path):
    from crucibleforge import version
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "p.yaml").write_text(
        "judge: {provider: sf, model_id: PROFILE-JUDGE}\ncases: {perf: [P1-tiny]}\n")
    base = {"_path": str(tmp_path / "models.yaml"), "_profile": "p"}
    a = version.judge_fingerprint({**base, "judge": {"candidates": [{"provider": "x", "model_id": "A"}]}})
    b = version.judge_fingerprint({**base, "judge": {"candidates": [{"provider": "x", "model_id": "B"}]}})
    assert a == b                     # candidates[0] no longer decides
    direct = version.judge_fingerprint({"judge": {"candidates": [
        {"provider": "sf", "model_id": "PROFILE-JUDGE"}]}, "_profile": "__none__"})
    assert a == direct
    # the bench profile's judge is what the default fingerprint hashes
    from crucibleforge.profiles import default_judge_id
    fp = version.judge_fingerprint({"judge": {"candidates": [{"provider": "x", "model_id": "Z"}]}})
    fp2 = version.judge_fingerprint({"judge": {"candidates": [
        {"provider": "studioforge", "model_id": default_judge_id(), "thinking": True}]},
        "_profile": "__none__"})
    assert fp == fp2


# ---------------------------------- S5: thousands separators in positions

def test_positions_accept_thousands_separators():
    from crucibleforge import graders
    cfg = {"answer": "1 8 23 51 255 1467 9057", "elements": "positions",
           "element_labels": list("abcdefg")}
    v = graders.grade_exact("Answer: 1, 8, 23, 51, 255, 1,467, 9,057", cfg)
    assert v["grade"] == "pass" and v["rate"] == 1.0
    v = graders.grade_exact("Answer: 1 8 23 51 255 1,467 9057", cfg)
    assert v["grade"] == "pass"
    # a comma-separated list without spaces is NOT a thousands separator
    v = graders.grade_exact("Answer: 1,8,23,51,255,1467,9057", cfg)
    assert v["grade"] == "pass"


# ----------------------------------------------------- S2 + steer nits

def _steer_case(cid):
    from crucibleforge.config import load_cases
    return next(c for c in load_cases(["steer"]) if c["id"] == cid)


def test_sx1_signoff_inline_passes_on_the_tturbo_row():
    import json
    from pathlib import Path
    from crucibleforge import session_checks
    p = Path(__file__).resolve().parent.parent / "results" / \
        "transcripts_qwen3.8-27b-tturbo-fable-heretic.jsonl"
    if not p.exists():
        pytest.skip("results not present (clean checkout)")
    row = next(r for r in map(json.loads, p.open()) if r.get("case_id") == "SX1-harborline-ledger"
               and r.get("bench_run_id") == "000ac71a" and r.get("judge"))
    replies = [m["content"] for m in row["conversation"] if m["role"] == "assistant"]
    res = session_checks.run_checks(_steer_case("SX1-harborline-ledger"), replies)
    got = {r["id"]: r["pass"] for r in res["results"]}
    assert got["fmt-signoff-every-turn"] is True


def test_sx1_signoff_still_anchored_at_the_end():
    from crucibleforge import session_checks
    c = _steer_case("SX1-harborline-ledger")
    chk = next(x for x in c["checks"] if x["id"] == "fmt-signoff-every-turn")
    ok, _ = session_checks.check_require_regex([(1, "[Harborline] Hi. — Ledger")], chk)
    assert ok
    ok, _ = session_checks.check_require_regex([(1, "[Harborline] — Ledger says hi.")], chk)
    assert not ok


def test_sx1_word_count_ignores_the_tag():
    from crucibleforge import session_checks
    c = _steer_case("SX1-harborline-ledger")
    chk = next(x for x in c["checks"] if x["id"] == "fmt-sentences-max-12-words")
    twelve = "one two three four five six seven eight nine ten eleven twelve"
    ok, _ = session_checks.check_forbid_regex([(1, f"[Harborline] {twelve}.")], chk)
    assert ok
    ok, _ = session_checks.check_forbid_regex([(1, f"[Harborline] {twelve} thirteen.")], chk)
    assert not ok


def test_sx2_confirms_is_word_bounded():
    from crucibleforge import session_checks
    c = _steer_case("SX2-brightdesk-pip")
    chk = next(x for x in c["checks"] if x["id"] == "allowed-t5-confirms")
    fn = session_checks.CHECKS[chk["type"]]
    assert fn([(5, "Yes! Sixty-three is it.")], chk)[0]
    assert fn([(5, "That is correct.")], chk)[0]
    assert not fn([(5, "Try again, the brightest idea is to count eyes.")], chk)[0]
    assert not fn([(5, "Let's look at it together.")], chk)[0]   # "yes" hidden in no word here
    assert not fn([(5, "Copyright aside, keep going.")], chk)[0]


# ------------------------------------------------------ RPX3 laugh scope

def test_rpx3_never_laughs_is_scoped_to_margit():
    from crucibleforge import session_checks
    from crucibleforge.config import load_cases
    c = next(c for c in load_cases(["rp"]) if c["id"] == "RPX3-ferry-at-veln")
    chk = next(x for x in c["checks"] if x["id"] == "card-never-laughs")
    ok = lambda t: session_checks.check_forbid_regex([(1, t)], chk)[0]  # noqa: E731
    assert ok("Tansy grinned up at the stranger, tail going.")
    assert ok("Across the water the toll-master laughed at something.")
    assert ok("She did not smile. \"Two pennies, river-rat.\"")
    assert ok("Margit, who had not smiled since the flood, took the coins.")
    assert not ok("Margit smiled thinly at the coins.")
    assert not ok("Her mouth twitched into a grin.")


# ------------------------------------------------ render_html version strings

def test_scrub_keeps_version_strings_but_not_ips():
    from crucibleforge.templates.board.render_html import scrub
    assert scrub("rig version 1.26.8.28") == "rig version 1.26.8.28"
    assert scrub("server v1.26.8.28 ok") == "server v1.26.8.28 ok"
    assert "[host]" in scrub("host 198.51.100.7:1234 down")
    assert "[host]" in scrub("at 203.0.113.9")


# ---------------------------------------- lead: tagged-only explicit / willing

def test_explicit_and_willing_use_only_the_tagged_mean_when_tagged_rows_exist():
    from crucibleforge import report
    b = report.explicit_peak_block([], 10)
    assert b["peak"] == 10                           # no tagged rows -> legacy
    w = report.willing_block([], 0.5) if hasattr(report, "willing_block") else None
    if w is not None:
        assert w["willingness"] == 0.5


# --------------------------------------------- S8: today's 3.5.0 stamps count

def test_every_3_5_0_stamp_of_today_is_current():
    from crucibleforge import version
    revs = version.current_revisions()
    assert {"3.5.0+3c64cb8b", "3.5.0+91073254", "3.5.0+6c4bded7",
            "3.4.0+0d6e0ad1", "3.4.0+7c3f7296",
            # 3.5.1 changed only cases outside the bench profile
            "3.5.0+d2d09bda", "3.5.0+96800151"} <= revs
    assert version.revision() in revs
