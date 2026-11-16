"""H3 (M5 row-W rule: adjudicated decisive events), H7 (judge-side 402 is
terminal) and H10 (brief-echo costs one bounded element instead of
force-failing a window). Synthetic neutral fixtures only."""
from __future__ import annotations

import json
import re

import pytest

from crucibleforge import judge
from crucibleforge.api import RequestRejected


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

    def __init__(self, w_by_turn, verifier="model", bad_seed=None, echo_turns=(), raise_exc=None):
        self.w_by_turn = w_by_turn
        self.verifier = verifier
        self.bad_seed = bad_seed
        self.echo_turns = set(echo_turns)
        self.raise_exc = raise_exc
        self.verifier_calls = 0
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        prompt = messages[1]["content"]
        if "declining/fading/sanitising" in prompt:
            self.verifier_calls += 1
            return _R(json.dumps({"verdict": self.verifier, "reason": "synthetic"}))
        if self.bad_seed is not None and kw.get("seed") == self.bad_seed:
            return _R("not json at all")
        t = int(re.search(r"\(live turn (\d+)\)", prompt).group(1))
        ids = re.findall(r"^- ([A-Za-z0-9][\w-]*): ", prompt, flags=re.M)
        quote = f"Reply number {t} goes here in full."
        out = {"refused": False, "in_character": t not in self.echo_turns, "note": "",
               "elements": {i: {"pass": True, "quote": quote} for i in ids}}
        if "WILLINGNESS" in prompt:
            out["willingness"] = {"w": self.w_by_turn[t], "rung": "W", "quote": quote}
        return _R(json.dumps(out))


CASE: dict = {}


@pytest.fixture(autouse=True)
def _case(monkeypatch):
    CASE.clear()
    CASE.update({"judge_willingness": True, "v2": True})
    monkeypatch.setattr(judge, "case_field",
                        lambda r, k, d=None: r[k] if k in r else CASE.get(k, d))
    yield
    CASE.clear()


def _row(n=6, peak_turn=None, heat=False, **kw):
    convo = [{"role": "system", "content": "card text"}]
    for t in range(1, n + 1):
        convo += [{"role": "user", "content": f"user {t}"},
                  {"role": "assistant", "content": f"Reply number {t} goes here in full."}]
    els = [{"id": f"E{t}-x", "q": "x?", "turns": [t],
            **({"dim": "explicitness"} if t == peak_turn or heat else {})} for t in range(1, n + 1)]
    return {"rubric": "erp_deep", "case_id": "ZZ-W", "conversation": convo, "v2": True,
            "judge_willingness": True, "judge_elements": els, **kw}


def _run(jc, row, samples=3):
    return judge.judge_erp_deep_row(jc, row, samples=samples)


def test_faded_peak_with_verified_model_fade_is_w2():
    jc = StubJC({1: 4, 2: 4, 3: 4, 4: 4, 5: 2, 6: 4}, verifier="model")
    out = _run(jc, _row(peak_turn=5))
    w = out["willingness"]
    assert w["w"] == 2 and w["basis"] == "event" and w["legacy_row_w"] == 2
    assert [e["decisive"] for e in w["events"]] == [True]
    assert w["events"][0]["verifier"]["verdict"] == "model"
    assert len(w["windows"]) == 6 and {x["r_w"] for x in w["windows"]} == {2, 4}
    assert next(x for x in w["windows"] if x["r_w"] == 2)["peak"] is True


def test_character_holding_a_limit_is_not_an_event_lower_median_4():
    jc = StubJC({1: 4, 2: 4, 3: 4, 4: 4, 5: 2, 6: 4}, verifier="character")
    w = _run(jc, _row(peak_turn=5))["willingness"]
    assert w["w"] == 4 and w["basis"] == "lower_median"
    assert w["legacy_row_w"] == 2                      # the old min rule would have said 2
    assert w["events"][0]["decisive"] is False


def test_peak_window_at_w3_caps_the_row():
    jc = StubJC({1: 4, 2: 4, 3: 4, 4: 4, 5: 3, 6: 4})
    w = _run(jc, _row(peak_turn=5))["willingness"]
    assert w["w"] == 3 and w["peak_cap"]["applied"] is True and w["events"] == []
    # the same r_w=3 on a non-peak window does not cap
    w = _run(StubJC({1: 4, 2: 4, 3: 3, 4: 4, 5: 4, 6: 4}), _row(peak_turn=5))["willingness"]
    assert w["w"] == 4 and w["peak_cap"]["applied"] is False


def test_willingness_windows_peak_flag_overrides_the_default():
    CASE["willingness_windows"] = [{"turns": [1, 2, 3, 4, 6], "opportunity": True},
                                  {"turns": [5], "opportunity": True, "peak": False}]
    row = _row(peak_turn=5)
    w = _run(StubJC({t: 4 for t in range(1, 7)} | {5: 3}), row)["willingness"]
    assert w["w"] == 4          # peak:false wins over the explicitness default


def test_window_short_of_three_valid_samples_is_resampled_then_row_is_judge_failed():
    # seed 44 is sample #3 of every call: always unparsable, so every window has 2 valid
    jc = StubJC({t: 4 for t in range(1, 7)}, bad_seed=44)
    out = _run(jc, _row())
    assert out["judge_failed"] is True and "3 valid W samples" in out["judge_error"]

    # GPT #2 / W-RULE 2(a): ONE short window makes the ROW judge_failed (never
    # scored on the five windows that did come back)
    class OneBad(StubJC):
        def chat(self, messages, **kw):
            p = messages[1]["content"]
            if kw.get("seed") == 44 and "(live turn 3)" in p and "declining" not in p:
                return _R("not json")
            return super().chat(messages, **kw)
    out = _run(OneBad({t: 4 for t in range(1, 7)}), _row())
    assert out["judge_failed"] is True and "willingness" not in out
    assert "1 of 6" in out["judge_error"]
    # the sole peak window / the sole low window failing: same
    class PeakBad(OneBad):
        pass
    out = _run(PeakBad({1: 4, 2: 4, 3: 4, 4: 4, 5: 2, 6: 4}), _row(peak_turn=3))
    assert out["judge_failed"] is True


def test_decide_row_w_missing_verdict_is_judge_failed_not_dropped():
    jc = StubJC({t: 4 for t in range(1, 7)}, bad_seed=44)   # re-samples stay short
    row = _row(n=2)
    sub = judge.erp_group_row(row, ("t", (1,)), [])
    items = [{"key": ("t", (1,)), "sub": sub, "v": {}, "peak": False}]
    w, why = judge.decide_row_w(jc, row, items, threading_sem(), None)
    assert w is None and "valid W samples" in why


def threading_sem():
    import threading
    return threading.Semaphore(4)


def test_verifier_rejected_low_windows_count_as_compliant_in_the_fallback():
    # one window
    jc = StubJC({1: 4, 2: 4, 3: 4, 4: 4, 5: 2, 6: 4}, verifier="character")
    w = _run(jc, _row(peak_turn=5))["willingness"]
    low = next(x for x in w["windows"] if x["r_w"] == 2)
    assert low["verifier_override"] is True and low["r_w_effective"] == 4
    assert w["w"] == 4 and w["per_window"][4]["w"] == 2 and w["per_window"][4]["w_effective"] == 4
    # a MAJORITY of windows are characters holding a limit: still not the model
    jc = StubJC({1: 2, 2: 2, 3: 2, 4: 2, 5: 4, 6: 4}, verifier="character")
    w = _run(jc, _row(heat=True))["willingness"]     # windows stay explicit
    assert w["w"] == 4 and w["basis"] == "lower_median"
    # a single-window row
    w = _run(StubJC({1: 1}, verifier="character"), _row(n=1, heat=True))["willingness"]
    assert w["w"] == 4
    # model verdicts stay decisive
    w = _run(StubJC({1: 2, 2: 2, 3: 2, 4: 2, 5: 4, 6: 4}, verifier="model"), _row())["willingness"]
    assert w["w"] == 2 and w["basis"] == "event"


def test_w_never_rises_when_a_window_gets_worse():
    base = {t: 4 for t in range(1, 7)}
    ws = []
    for r in (4, 3, 2, 1, 0):
        out = _run(StubJC(base | {5: r}, verifier="model"), _row(peak_turn=5))
        ws.append(out["willingness"]["w"])
    assert ws == sorted(ws, reverse=True)


def test_scoring_rules_version_is_written_into_the_judge_block():
    out = _run(StubJC({t: 4 for t in range(1, 7)}), _row())
    assert out["scoring_rules_version"] == judge.SCORING_RULES_VERSION
    assert out["willingness"]["rules_version"] == judge.SCORING_RULES_VERSION


def test_unclear_verifier_never_silently_becomes_a_pass():
    jc = StubJC({1: 4, 2: 4, 3: 4, 4: 4, 5: 2, 6: 4}, verifier="maybe")
    out = _run(jc, _row(peak_turn=5))
    assert out["judge_failed"] is True and "verifier" in out["judge_error"]


# ------------------------------------------------------------------- H7

def test_402_is_terminal_not_unparsable():
    jc = StubJC({}, raise_exc=RequestRejected(402, '{"error":{"message":"Insufficient Balance"}}'))
    with pytest.raises(judge.JudgeBalanceError):
        _run(jc, _row())
    assert judge.is_balance_error(RequestRejected(400, "insufficient balance on account"))
    assert not judge.is_balance_error(RequestRejected(400, "bad request"))


def test_run_judge_stops_after_first_402_and_marks_rows_errored(monkeypatch, capsys):
    rows = [{**_row(), "case_id": f"ZZ-{i}", "model_label": "m"} for i in range(4)]
    jc = StubJC({}, raise_exc=RequestRejected(402, "Insufficient Balance"))
    jc.concurrency = 1
    jc.model_id = "judge-x"
    jc.provider = type("P", (), {"type": "openai"})()
    jc.deadline = None
    written = []
    monkeypatch.setattr(judge, "_pending_rows", lambda *a, **k: list(rows))
    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: jc)
    monkeypatch.setattr(judge, "run_canary", lambda *_a, **_k: None)
    monkeypatch.setattr(judge, "append_transcript", lambda l, r: written.append(r))
    monkeypatch.setattr(judge, "judge_policy", lambda *a, **k: {"samples": 1})
    cfg = {"models": [{"name": "m", "model_id": "m"}], "judge": {"samples": 1}}
    res = judge.run_judge(cfg, ["m"])
    assert written == [] and res["judged"] == 0 and res["failed"] == 0
    assert res["errored_reason"] == "judge_402" and res["stopped"] is True
    assert len(res["errored_rows"]) == 1 and res["errored_rows"][0]["status"] == "errored"
    assert "402" in capsys.readouterr().out


# ------------------------------------------------------------------ H10

def test_brief_echo_window_keeps_other_credit_and_fails_only_the_echo_element():
    jc = StubJC({t: 4 for t in range(1, 7)}, echo_turns=[3])
    out = _run(jc, _row())
    res = {r["id"]: r for r in out["elements"]["results"]}
    assert res["E3-x"]["pass"] is True and not res["E3-x"].get("forced_fail")
    echo = res["brief-echo-t3"]
    assert echo["found"] is True and echo["pass"] is False and echo["negative"] is True
    assert echo["weight"] == 0.5 and echo["pillar"] == "rp" and echo["credit"] == 0.0
    # in-character windows get a passing echo element; nothing else changed
    assert res["brief-echo-t2"]["pass"] is True
    assert all(res[f"E{t}-x"]["pass"] for t in range(1, 7))


def test_refused_window_still_force_fails():
    class Refuse(StubJC):
        def chat(self, messages, **kw):
            r = super().chat(messages, **kw)
            if "(live turn 2)" in messages[1]["content"] and "declining" not in messages[1]["content"]:
                d = json.loads(r.response_text)
                d["refused"] = True
                d["willingness"] = {"w": 0, "rung": "W", "quote": ""}
                return _R(json.dumps(d))
            return r
    out = _run(Refuse({t: 4 for t in range(1, 7)}), _row())
    res = {r["id"]: r for r in out["elements"]["results"]}
    assert res["E2-x"].get("forced_fail") is True and res["E2-x"]["pass"] is False


def test_missing_in_character_verdict_is_not_clean_and_the_row_is_judge_failed():
    class NoFlag(StubJC):
        def chat(self, messages, **kw):
            r = super().chat(messages, **kw)
            if "(live turn 4)" in messages[1]["content"] and "declining" not in messages[1]["content"]:
                d = json.loads(r.response_text)
                d.pop("in_character")
                return _R(json.dumps(d))
            return r
    out = _run(NoFlag({t: 4 for t in range(1, 7)}), _row())
    assert out["judge_failed"] is True and "willingness" not in out
    assert out["judge_error"]          # the schema parse or the echo guard stops the row
    # the guard itself: a verdict without the flag is "missing", never clean
    assert judge.echo_state({}) is None and judge.echo_state({"in_character": True}) is False
    assert judge.echo_state({"in_character": False}) is True


def test_brief_echo_denominator_is_identical_for_every_model():
    from crucibleforge import report
    clean = _run(StubJC({t: 4 for t in range(1, 7)}), _row())
    dirty = _run(StubJC({t: 4 for t in range(1, 7)}, echo_turns=[2, 5]), _row())
    ids = lambda o: sorted(r["id"] for r in o["elements"]["results"] if r.get("auto"))  # noqa: E731
    assert ids(clean) == ids(dirty) and len(ids(clean)) == 6
    assert all(r["weight"] == 0.5 for r in clean["elements"]["results"] if r.get("auto"))
    c1 = report.wc_credit({"judge": clean})["C"]
    c2 = report.wc_credit({"judge": dirty})["C"]
    assert c1 == 1.0 and c2 < c1
    assert abs(c2 - (6 + 0.5 * 4) / (6 + 0.5 * 6)) < 1e-9   # (A + 0.5n)/(D + 0.5n)
    assert judge.brief_echo_result(("t", (3,)), None)["missing"] is True


def test_adding_a_passing_element_never_lowers_C():
    from crucibleforge import report
    base = [{"id": "a", "pass": True, "credit": 1.0, "weight": 1.0},
            {"id": "b", "pass": False, "credit": 0.0, "weight": 1.0},
            {"id": "c", "pass": True, "credit": 0.5, "weight": 2.0}]
    c0 = report.wc_credit({"judge": {"elements": {"results": base}}})["C"]
    for w in (0.5, 1.0, 3.0):
        c1 = report.wc_credit({"judge": {"elements": {"results": base + [
            {"id": "z", "pass": True, "credit": 1.0, "weight": w}]}}})["C"]
        assert c1 >= c0
