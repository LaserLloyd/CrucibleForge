"""Fix pass, round 3 (Opus review 2 + GPT review 2). Every test drives the real
path (stub judge that sees the real prompt, real score_row) and fails without
its fix. Synthetic neutral fixtures only."""
from __future__ import annotations

import json
import re

import pytest

from crucibleforge import config, judge, lint, ladder, report, rpdeep, spend, version


class _R:
    finish_reason = "stop"
    completion_tokens = 10
    reasoning_tokens = 0
    reasoning_text = ""

    def __init__(self, text):
        self.response_text = text


class StubJC:
    thinking = False
    context_length = 16384
    max_tokens = 8192
    row_timeout_s = None
    sample_temperature = 0.7
    concurrency = 4
    extra_body = {}

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def chat(self, messages, **kw):
        prompt = messages[1]["content"]
        if "declining/fading/sanitising" in prompt:      # H3 event verifier: the model's own fade
            return _R(json.dumps({"verdict": "model", "reason": "stub"}))
        self.prompts.append(prompt)
        ids = re.findall(r"^- ([A-Za-z0-9][\w-]*): ", prompt, flags=re.M)
        return _R(json.dumps(self.answer(ids, prompt)))


CASE: dict = {}


@pytest.fixture(autouse=True)
def _case(monkeypatch):
    CASE.clear()
    monkeypatch.setattr(judge, "case_field",
                        lambda r, k, d=None: r[k] if k in r else CASE.get(k, d))
    yield
    CASE.clear()


def _turns_in(prompt):
    return {int(m) for m in re.findall(r"\(live turn (\d+)\)", prompt)}


def _session(n, rubric="erp_deep", **kw):
    convo = [{"role": "system", "content": "card text"}]
    for t in range(1, n + 1):
        convo += [{"role": "user", "content": f"user {t}"},
                  {"role": "assistant", "content": f"Reply number {t} goes here in full."}]
    CASE.update({k: v for k, v in kw.items() if k not in ("judge_elements", "rubric")})
    CASE["v2"] = True
    return {"rubric": rubric, "case_id": "ZZ-R3", "conversation": convo, "v2": True, **kw}


Q = "Reply number 1 goes here in full."


def _ans(bad=(), refuse=True, neg_found_on=(), dims=None):
    """A window whose prompt shows any turn of ``bad`` comes back refused / out of
    character / W0; negative elements (ids starting with D) are 'found' when
    their window shows a turn of ``neg_found_on``."""
    def f(ids, prompt):
        shown = _turns_in(prompt)
        is_bad = bool(shown & set(bad))
        out = {**(dims or {}), "refused": is_bad and refuse,
               "in_character": not (is_bad and refuse), "note": "",
               "elements": {i: {"pass": (bool(shown & set(neg_found_on)) if i.startswith("D") else True),
                                "quote": f"Reply number {min(shown or {1})} goes here in full."}
                         for i in ids}}
        if "WILLINGNESS" in prompt:
            out["willingness"] = {"w": 0 if is_bad else 4, "rung": "W",
                                  "quote": f"Reply number {min(shown or {1})} goes here in full."}
        return out
    return f


def _els(*turns):
    return [{"id": f"E{t}-x", "q": "x?", "turns": [t]} for t in turns]


# ------------------------------------------------------------ M1 (rp_deep)

def _deep_fixture(element_extra, replies, quote, in_character=True):
    e = {"id": "A1-x", "q": "x?", "turns": [1], "fact": "A1", **element_extra}
    convo = [{"role": "system", "content": "card"}]
    for i, r in enumerate(replies, start=1):
        convo += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": r}]
    row = {"rubric": "rp_deep", "case_id": "ZZ-R3-DEEP", "conversation": convo,
           "judge_elements": [e], "judge_key": "key"}
    case = {"id": "ZZ-R3-DEEP", "turns": [f"u{i}" for i in range(1, len(replies) + 1)],
            "judge_elements": [e], "checks": []}

    def ans(ids, prompt):
        if not ids:
            return {"voice": 2, "craft": 2, "initiative": 2, "refused": False, "note": ""}
        return {"refused": False, "in_character": in_character, "note": "",
                "elements": {i: {"pass": True, "quote": quote} for i in ids}}
    row["judge"] = judge.judge_deep_row(StubJC(ans), row, samples=1)
    return row, case


def _item(row, case):
    sc = rpdeep.score_row(row, case, [])
    return next(i for i in sc["items"] if i["id"] == "A1-x"), sc


SPOKEN = 'She grinned at him. "Ottavio is the dog\'s name, kid, and he bites." Then she left.'


def test_m1_out_of_character_group_stays_failed_through_score_row():
    row, case = _deep_fixture({}, [SPOKEN, SPOKEN], "Ottavio is the dog's name, kid, and he bites",
                              in_character=False)
    res = row["judge"]["elements"]["results"][0]
    assert res["pass"] is False and res["credit"] == 0.0         # the stored verdict agrees
    it, _ = _item(row, case)
    # long scenes D1 (b), lead 2026-11-19: the judge's own out-of-character flag no longer zeroes a fact alone (only
    # the format gate or the refusal pattern does); the fact keeps the quote-verified result and the row lists it
    assert it["pass"] is True


def test_m1_dialogue_scope_failure_stays_failed():
    narr = 'She remembered the dog named Ottavio from long ago. "Not today," she said.'
    row, case = _deep_fixture({"quote_scope": "dialogue"}, [narr, narr],
                              "She remembered the dog named Ottavio from long ago")
    res = row["judge"]["elements"]["results"][0]
    assert res["pass"] is False and res["credit"] == 0.0
    assert _item(row, case)[0]["pass"] is False


def test_m1_veto_fails_the_item_even_with_a_stored_credit():
    row, case = _deep_fixture({"veto": "Ottavio"}, [SPOKEN, SPOKEN],
                              "Ottavio is the dog's name, kid, and he bites")
    assert row["judge"]["elements"]["results"][0]["credit"] == 1.0      # judged YES, verified
    it, _ = _item(row, case)
    assert it["pass"] is False and "veto" in it["detail"]


def test_m1_short_quote_rescue_passes_the_item():
    rep = 'She shrugged. "The dog is called Ottavio, kid, and that is all I know."'
    row, case = _deep_fixture({}, [rep, rep], "Ottavio.")
    res = row["judge"]["elements"]["results"][0]
    assert res["pass"] is False and res["judge_pass"] is True           # the guard rejected it
    it, sc = _item(row, case)
    assert it["pass"] is True and "short quote" in it["detail"]


def test_m1_stale_credit_never_overrides_the_verdict():
    """A stored verdict written before this pass: pass flipped, credit stale."""
    row, case = _deep_fixture({}, [SPOKEN, SPOKEN], "Ottavio is the dog's name, kid, and he bites")
    res = row["judge"]["elements"]["results"][0]
    res.update({"pass": False, "judge_pass": False, "credit": 1.0})
    assert _item(row, case)[0]["pass"] is False
    res.update({"pass": True, "credit": 0.0})
    assert _item(row, case)[0]["pass"] is True


def test_set_pass_keeps_scores_and_found_consistent():
    neg = {"negative": True, "scale": 2}
    r = judge.set_pass({"id": "x", "pass": True, "scale": 2, "score": 0, "negative": True,
                        "found": False, "credit": 1.0}, neg, False)
    assert (r["pass"], r["credit"], r["score"], r["found"]) == (False, 0.0, 2, True)
    p = judge.set_pass({"id": "y", "pass": False, "scale": 2, "score": 0, "credit": 0.0},
                       {"scale": 2}, True)
    assert (p["pass"], p["credit"], p["score"]) == (True, 1.0, 2)


# ------------------------------------------------------------ M2

def test_m2_every_window_element_does_not_block_opportunity_exclusion():
    els = [{"id": "A1-x", "q": "x?", "turns": [1, 2], "opportunity": False},
           {"id": "B1-x", "q": "x?", "turns": [3, 4]},
           {"id": "D1-neg", "q": "d?", "window": "all", "negative": True}]
    row = _session(4, judge_elements=els, judge_willingness=True)
    out = judge.judge_erp_deep_row(StubJC(_ans(bad=(1, 2), refuse=False)), row)
    assert [g.get("w_excluded") for g in out["groups"]] == [True, None]
    assert out["willingness"]["w"] == 4            # was 1: the pacing beat counted


def test_m2_untagged_elements_do_not_block_exclusion_on_the_long_path():
    dims = {"identity": 7, "erotic": 7, "continuity": 7, "ooc": 8, "voice": 7, "prose": 7,
            "explicitness": 5, "sanitized": False, "recalled_detail": True, "limit_violated": False}
    els = [{"id": "L1-l", "q": "l?"}, {"id": "P1-p", "q": "p?", "turns": [1, 2], "opportunity": False}]
    row = _session(12, rubric="erp_session", judge_elements=els, judge_willingness=True)
    out = judge.judge_long_row(StubJC(_ans(bad=(1,), refuse=False, dims=dims)), row)
    assert out["long"]["windows"][0].get("w_excluded") is True
    assert out["willingness"]["w"] == 4


# ------------------------------------------------------------ M3 / GPT new 1-2

def test_m3_exempt_turn_inside_a_mixed_window_is_ignored_by_refusal_and_force_fail():
    els = [{"id": "E12-x", "q": "x?", "turns": [1, 2]}]
    row = _session(2, judge_elements=els, judge_willingness=True)
    base = judge.judge_erp_deep_row(StubJC(_ans(bad=(2,))), row)
    assert base["refused"] is True and base["willingness"]["w"] == 0          # no exemption
    row["exempt_turns"] = CASE["exempt_turns"] = [2]
    out = judge.judge_erp_deep_row(StubJC(_ans(bad=(2,))), row)
    assert out["refused"] is False
    assert out["willingness"]["w"] == 4
    assert not any(r.get("forced_fail") for r in out["elements"]["results"])
    assert out["groups"][0]["exempt"] is True and out["groups"][0]["residual_turns"] == [1]


def test_m3_exempt_step_out_does_not_set_row_w():
    row = _session(3, judge_elements=_els(1, 2, 3), judge_willingness=True,
                   exempt_turns=[3])
    out = judge.judge_erp_deep_row(StubJC(_ans(bad=(3,))), row)
    assert out["refused"] is False and out["willingness"]["w"] == 4          # was W0
    assert [g.get("exempt") for g in out["groups"]] == [None, None, True]


def test_m3_exempt_step_out_row_w_feeds_wc_row():
    """The Opus recipe: refused is False but W 0 and S 0 before the fix."""
    row = _session(3, judge_elements=_els(1, 2, 3), judge_willingness=True, exempt_turns=[3])
    out = judge.judge_erp_deep_row(StubJC(_ans(bad=(3,))), row)
    r = {"judge": out, "rubric": "erp_deep", "judge_willingness": True, "v2": True,
         "exempt_turns": [3], "checks": {"results": []}, "conversation": row["conversation"],
         "response": "x"}
    assert report.wc_row(r)["w"] == 4


def test_m3_every_window_negative_never_sees_the_exempt_turn():
    els = _els(1, 2, 3) + [{"id": "D1-neg", "q": "d?", "window": "all", "negative": True}]
    row = _session(3, judge_elements=els, judge_willingness=True, exempt_turns=[3])
    out = judge.judge_erp_deep_row(StubJC(_ans(bad=(3,), neg_found_on=(3,))), row)
    d = next(r for r in out["elements"]["results"] if r["id"] == "D1-neg")
    assert d["pass"] is True                       # the step-out is not "drift"


def test_m3_whole_row_group_is_exempt_at_turn_level():
    els = [{"id": "D1-neg", "q": "d?", "window": "all", "negative": True}]
    row = _session(3, judge_elements=els, judge_willingness=True, exempt_turns=[2])
    out = judge.judge_erp_deep_row(StubJC(_ans(bad=(2,), neg_found_on=(2,))), row)
    assert out["refused"] is False and out["willingness"]["w"] == 4
    assert next(r for r in out["elements"]["results"] if r["id"] == "D1-neg")["pass"] is True


def test_m3_willingness_window_turn_inside_a_mixed_window():
    els = [{"id": "E12-x", "q": "x?", "turns": [1, 2]}]
    row = _session(2, judge_elements=els, judge_willingness=True,
                   willingness_windows=[{"turns": [2], "opportunity": False, "why": "pacing"}])
    out = judge.judge_erp_deep_row(StubJC(_ans(bad=(2,), refuse=False)), row)
    assert out["willingness"]["w"] == 4            # containment matching ignored it
    assert out["groups"][0]["w_excluded"] is True


def test_m3_all_windows_ineligible_gives_no_rung_not_w0_or_w4():
    row = _session(2, judge_elements=_els(1, 2), judge_willingness=True, exempt_turns=[1, 2])
    out = judge.judge_erp_deep_row(StubJC(_ans(bad=(1, 2))), row)
    assert out.get("w_no_opportunity") is True and "willingness" not in out
    assert out["judge_failed"] is False and out["refused"] is False


DIMS = {"identity": 7, "erotic": 7, "continuity": 7, "ooc": 8, "voice": 7, "prose": 7,
        "explicitness": 5, "sanitized": False, "recalled_detail": True, "limit_violated": False}


def test_m3_long_path_scripted_step_out_in_a_four_turn_window():
    """Opus probe: a 10-turn erp_session, a scripted OOC step-out at turn 6."""
    row = _session(10, rubric="erp_session", judge_elements=[{"id": "L1-l", "q": "l?"}],
                   judge_willingness=True)
    stub = lambda: StubJC(_ans(bad=(6,), dims=DIMS))                 # noqa: E731
    base = judge.judge_long_row(stub(), row)
    assert base["refused"] is True and base["willingness"]["w"] == 0
    row["exempt_turns"] = CASE["exempt_turns"] = [6]
    row["willingness_windows"] = CASE["willingness_windows"] = [
        {"turns": [6], "opportunity": False, "why": "scripted step-out"}]
    out = judge.judge_long_row(stub(), row)
    assert out["refused"] is False and out["willingness"]["w"] == 4
    mixed = [m for m in out["long"]["windows"] if m.get("residual_turns")]
    assert mixed and 6 not in mixed[0]["residual_turns"]


def test_m3_long_path_exempt_alone_does_not_zero_w():
    row = _session(10, rubric="erp_session", judge_elements=[{"id": "L1-l", "q": "l?"}],
                   judge_willingness=True, exempt_turns=[6])
    out = judge.judge_long_row(StubJC(_ans(bad=(6,), dims=DIMS)), row)
    assert out["refused"] is False and out["willingness"]["w"] == 4


# ------------------------------------------------------------ S1 / S6 / S3 / S4 / S5 / N5

def test_s1_long_path_out_of_character_window_force_fails_its_elements():
    els = [{"id": "T6-t", "q": "t?", "turns": [5]},
           {"id": "T1-t", "q": "t?", "turns": [1]},
           {"id": "L1-l", "q": "l?"}]
    row = _session(10, rubric="erp_session", judge_elements=els)
    out = judge.judge_long_row(StubJC(_ans(bad=(5,), dims=DIMS)), row)
    res = {r["id"]: r for r in out["elements"]["results"]}
    assert res["T6-t"]["pass"] is False and res["T6-t"].get("forced_fail")
    assert res["T1-t"]["pass"] is True
    assert res["L1-l"]["pass"] is True              # passes in ANY window


def test_s6_exempt_and_excluded_windows_are_not_rejudged_for_low_w():
    row = _session(3, judge_elements=_els(1, 2, 3), judge_willingness=True, exempt_turns=[3])
    jc = StubJC(_ans(bad=(3,)))
    judge.judge_erp_deep_row(jc, row)
    calls3 = [p for p in jc.prompts if 3 in _turns_in(p)]
    assert len(calls3) == 1                         # no decisive low-W re-judge samples


def test_s3_collapse_sections_keeps_the_worst_negative_score():
    e = {"id": "N-x", "q": "q?", "sections": ["A", "B"], "negative": True, "scale": 2}
    rs = [{"id": "N-x", "pass": False, "found": True, "negative": True, "scale": 2, "score": 1,
           "credit": 0.5},
          {"id": "N-x", "pass": False, "found": True, "negative": True, "scale": 2, "score": 2,
           "credit": 0.0}]
    for order in (rs, rs[::-1]):
        out = judge._collapse_sections([e], list(order))
        assert len(out) == 1 and out[0]["credit"] == 0.0 and out[0]["score"] == 2


def test_s4_missing_evidence_fail_holds_on_negative_scale_two_elements():
    e = {"id": "N-x", "q": "q?", "negative": True, "scale": 2, "missing_evidence": "fail"}
    verdicts = [{"elements": {"N-x": {"pass": True, "quote": "nothing like the text", "score": 2}}}
                for _ in range(3)]
    out = judge._aggregate_elements(verdicts, [e], "The reply says something else entirely here.")
    assert out["N-x"]["score"] >= 1 and out["N-x"]["pass"] is True        # found = the failure
    res = judge.apply_elements({"response": "The reply says something else entirely here."}, {},
                               [e], out, "erp_deep")["results"][0]
    assert res["pass"] is False and res["credit"] < 1.0


def test_s5_single_call_v2_row_gets_fail_closed_quotes():
    row = _session(2, judge_elements=[{"id": "A1-x", "q": "x?", "quote_turns": [9]}])
    els = judge.row_elements(row)
    assert all(e.get("v2") is True for e in els)
    assert judge.element_quote_text(row, els[0]) == ""            # no turn 9: no evidence


def test_n5_sample_sd_sees_half_credit():
    e = {"id": "C-x", "q": "c?", "scale": 2}
    ve = {"C-x": {"verified_samples": [0, 1, 2], "n_samples": 3, "sample_scores": [1, 1, 2],
                  "sample_scores_by_sample": [2, 1, 1]}}
    sd = judge._sample_sd([e], ve)
    assert sd and sd > 0                       # half credit is not full credit


# ------------------------------------------------------------ GPT 7: spend

@pytest.fixture
def capenv(monkeypatch):
    monkeypatch.delenv(spend.LEDGER_ENV, raising=False)
    spend.reset()
    yield monkeypatch
    spend.reset()


def test_gpt7_admission_includes_the_incoming_estimate(capenv):
    capenv.setenv(spend.CAP_ENV, "15")
    with pytest.raises(spend.SpendCapExceeded):
        spend.reserve("openai", "m", 20.0)                  # a $20 call on a fresh $15 cap
    tok = spend.reserve("openai", "m", 9.0)
    with pytest.raises(spend.SpendCapExceeded):
        spend.reserve("openai", "m", 7.0)                   # 9 held + 7 > 15
    spend.release(tok)


def test_gpt7_ledger_admission_includes_the_incoming_estimate(capenv, tmp_path):
    capenv.setenv(spend.CAP_ENV, "1")
    capenv.setenv(spend.LEDGER_ENV, str(tmp_path / "ledger.jsonl"))
    spend.reset()
    with pytest.raises(spend.SpendCapExceeded):
        spend.reserve("openai", "m", 1.5)
    spend.release(spend.reserve("openai", "m", 0.9))


def test_gpt7_unpriced_success_is_charged_the_estimate(capenv):
    class Res:
        prompt_tokens = completion_tokens = None
    capenv.setenv(spend.CAP_ENV, "5")
    assert spend.record("openai", "m", None, Res(), estimate=0.7) == pytest.approx(0.7)
    assert spend.spent() == pytest.approx(0.7)
    assert spend.record("openai", "m", None, Res()) == pytest.approx(spend.FALLBACK_ESTIMATE_USD)


# ------------------------------------------------------------ GPT 8 + scratch marker

def test_gpt8_results_env_counts_as_a_live_destination(monkeypatch, tmp_path):
    d = tmp_path / "anywhere"
    d.mkdir()
    monkeypatch.setenv("CRUCIBLEFORGE_RESULTS", str(d))
    assert str(d.resolve()) in config.live_result_dirs()
    assert config.staged_isolated(d) is False               # an EMPTY live dir still refused
    (d / config.SCRATCH_MARKER).write_text("", encoding="utf-8")
    assert config.staged_isolated(d) is True


def test_gpt8_cli_guard_refuses_an_empty_env_destination(monkeypatch, tmp_path):
    import argparse
    from crucibleforge import cli
    d = tmp_path / "empty-live"
    d.mkdir()
    monkeypatch.setenv("CRUCIBLEFORGE_RESULTS", str(d))
    monkeypatch.setattr(config, "RESULTS_DIR", d)
    monkeypatch.delenv(ladder.STAGED_ENV, raising=False)
    with pytest.raises(SystemExit, match="--results"):
        cli._apply_process_switches(argparse.Namespace(include_staged=True, spend_cap_usd=None))


def test_scratch_init_creates_the_marker_and_refuses_the_board(monkeypatch, tmp_path):
    d = config.init_scratch(tmp_path / "scratch")
    assert (d / config.SCRATCH_MARKER).is_file()
    board = tmp_path / "board"
    board.mkdir()
    (board / "report.json").write_text("{}", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.init_scratch(board)
    assert not (board / config.SCRATCH_MARKER).exists()


def test_scratch_dir_survives_its_own_first_run_rows(monkeypatch, tmp_path):
    """The first staged invocation initialises the dir; rows it writes (meta,
    transcripts) must not turn it 'live' on the next invocation."""
    import argparse
    from crucibleforge import cli
    d = tmp_path / "scratch"
    monkeypatch.setattr(config, "RESULTS_DIR", d)
    monkeypatch.delenv(ladder.STAGED_ENV, raising=False)
    cli._apply_process_switches(argparse.Namespace(include_staged=True, spend_cap_usd=None))
    assert (d / config.SCRATCH_MARKER).is_file()
    (d / "meta_m.json").write_text("{}", encoding="utf-8")
    (d / "transcripts_m.jsonl").write_text("", encoding="utf-8")
    cli._apply_process_switches(argparse.Namespace(include_staged=True, spend_cap_usd=None))


def test_scratch_init_command(tmp_path):
    import argparse
    from crucibleforge import cli
    rc = cli.cmd_scratch_init(argparse.Namespace(dir=str(tmp_path / "s2")), {})
    assert rc == 0 and (tmp_path / "s2" / config.SCRATCH_MARKER).is_file()


def test_library_include_staged_fails_closed_on_the_live_board(monkeypatch, tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    (live / "report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "RESULTS_DIR", live)
    monkeypatch.setenv(ladder.STAGED_ENV, "1")
    assert ladder.include_staged() is False                  # N6: no cli in the loop
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path / "scratch")
    assert ladder.include_staged() is True


# ------------------------------------------------------------ GPT 9

def test_gpt9_card_would_clamp_needs_at_most_eight_turns():
    class Tiny:
        context_length = 4096
        max_tokens = 1024
    big = "card word " * 4000

    def mk(n):
        convo = [{"role": "system", "content": big}]
        for t in range(1, n + 1):
            convo += [{"role": "user", "content": f"u{t}"}, {"role": "assistant", "content": f"a{t}"}]
        return {"rubric": "rp_session", "case_id": "ZZ-R3", "conversation": convo}
    CASE["v2"] = True
    assert judge.card_would_clamp(Tiny(), mk(4)) is True
    assert judge.card_would_clamp(Tiny(), mk(judge.LONG_ROW_TURNS)) is True
    assert judge.card_would_clamp(Tiny(), mk(judge.LONG_ROW_TURNS + 1)) is False


# ------------------------------------------------------------ GPT 11 / Opus S2: hash

def test_gpt11_framing_switches_move_the_grading_hash_only_when_they_change_judging():
    base = {"id": "ZZ", "rubric": "erp_deep", "judge_elements": [{"id": "a", "q": "q"}]}
    h = lambda c: version._h8(version._grading_view(c))      # noqa: E731
    assert h({**base, "sexual": True}) != h(base)           # the sexual line enters the prompt
    assert h({**base, "sexual": False}) == h(base)          # neutral stays neutral
    assert h({**base, "v2": True}) != h(base)               # the judging regime changed
    assert h({**base, "v2": False}) == h(base)
    other = {"id": "ZZ", "rubric": "nsfw", "judge_elements": [{"id": "a", "q": "q"}]}
    assert h({**other, "sexual": True}) == h(other)         # no erp_deep line, no change
    # a case that carries neither field hashes exactly as before
    assert version._grading_view(base) == {k: base[k] for k in ("rubric", "judge_elements")}


def test_gpt11_public_case_hashes_are_unchanged():
    """Every public case lacks v2 / erp_deep+sexual, so its view is the old one."""
    for cid, c in _public_cases():
        old = {k: c[k] for k in version._JUDGE_GRADING_KEYS if k in c}
        els = old.get("judge_elements")
        # a standard-45 case (A5: CC4, NC2) also drops the 4.5 score-only element keys
        drop = version._ELEMENT_SCORE_ONLY_KEYS + (
            version._ELEMENT_SCORE_ONLY_KEYS_45 if c.get("element_standard") == 45 else ())
        if isinstance(els, list):
            old["judge_elements"] = [{k: v for k, v in e.items() if k not in drop}
                                     if isinstance(e, dict) else e for e in els]
        assert version._grading_view(c) == old, cid


def _public_cases():
    out = []
    for p in sorted(version._CASES_DIR.glob("*.json")):
        data = json.loads(p.read_bytes())
        for c in data if isinstance(data, list) else data.get("cases", []):
            if isinstance(c, dict) and c.get("id"):
                out.append((c["id"], c))
    return out


# ------------------------------------------------------------ lint

def _lint_case(**el):
    e = {"id": "A1-x", "q": "q?", "ref": "r", "fact": "F1", "turns": [1], **el}
    return {"id": "ZZ", "v2": True, "rubric": "erp_deep", "turns": ["a", "b"],
            "cast": [{"name": "N", "age": 30}], "system": "N is 30.", "prompt": "",
            "facts": [{"id": "F1", "kind": "canon", "text": "t"}], "judge_elements": [e]}


@pytest.mark.parametrize("fld,val", [("missing_evidence", ["fail"]), ("polarity", ["negative"]),
                                     ("evidence", "sometimes"), ("missing_evidence", "maybe")])
def test_lint_enum_lists_are_allowed_only_for_evidence(fld, val):
    probs = lint.v2_case_problems(_lint_case(**{fld: val}))
    assert any("v2 schema" in p and fld in p for p in probs), probs


def test_lint_evidence_list_of_excerpts_is_still_fine():
    probs = lint.v2_case_problems(_lint_case(evidence=[{"src": "card", "text": "t"}]))
    assert not [p for p in probs if "v2 schema" in p]


@pytest.mark.parametrize("field,val,needle", [
    ("willingness_windows", "turns 1-2", "must be a list"),
    ("willingness_windows", [5], "must be an object"),
    ("willingness_windows", [{"turns": [1], "section": "A", "opportunity": False}], "exactly one"),
    ("willingness_windows", [{"opportunity": False}], "exactly one"),
    ("willingness_windows", [{"turns": ["x"], "opportunity": False}], "not a turn number"),
    ("willingness_windows", [{"turns": [9], "opportunity": False}], "outside 1..2"),
    ("willingness_windows", [{"turns": [1], "opportunity": "no"}], "opportunity must be"),
    ("willingness_windows", [{"turns": [1], "opportunity": False, "why": ""}], "why must be"),
    ("willingness_windows", [{"turns": [1], "oops": 1}], "unknown key"),
    ("exempt_turns", "2", "must be a list"),
    ("exempt_turns", [0], "outside"),
    ("exempt_turns", [True], "not a turn number"),
    ("judge_preamble", "  ", "judge_preamble"),
    ("judge_preamble", 5, "judge_preamble"),
    ("sexual", "yes", "sexual must be"),
])
def test_lint_validates_the_wp8_field_shapes(field, val, needle):
    c = _lint_case()
    c[field] = val
    assert any(needle in p for p in lint.v2_case_problems(c)), lint.v2_case_problems(c)


def test_lint_accepts_well_formed_wp8_fields():
    c = _lint_case()
    c.update(willingness_windows=[{"turns": [1], "opportunity": False, "why": "pacing"},
                                  {"turns": [2], "opportunity": True, "why": "the ask"}],
             exempt_turns=[2], judge_preamble="neutral_facts", sexual=True)
    assert not [p for p in lint.v2_case_problems(c) if "WP8" in p]
