"""H1/H2/H4 measurement fixes (2026-11-08): repetition tiers, every-window
mean aggregation, the finish=length judge retry, term_density lint note.
All fixtures are synthetic and non-explicit."""
import json

from crucibleforge import judge, lint
from crucibleforge.session_checks import check_repetition_ngram


# ------------------------------------------------------------------ H1
def _loop(n_paras):
    return "\n\n".join("The lamp swung over the harbour wall tonight and %s drifted." % w
                       for w in ["gulls", "fog", "rain", "smoke"][:n_paras])


def test_repetition_tiers_differ_for_pieces_and_sessions():
    ok, detail, ex = check_repetition_ngram([(1, "A calm line about tea.\n\nA different one about maps.")], {})
    assert ex["credit"] == 1.0 and "full <= 26" in detail
    _, detail, _ = check_repetition_ngram([(1, "a b c d"), (2, "e f g h")], {})
    assert "full <= 34" in detail


def test_repetition_check_overrides_win():
    _, detail, ex = check_repetition_ngram([(1, _loop(4))], {"t1": 1, "t2": 2})
    assert "full <= 1" in detail and ex["credit"] == 0.0


# ------------------------------------------------------------------ H2
def _res(i, score):
    return {"id": "cr1", "score": score, "pass": score >= 1, "window": i}


def test_positive_scaled_every_window_is_mean_credit():
    e = {"id": "cr1", "scale": 2, "every_window": True}
    r = judge.worst_window(e, [_res(i, s) for i, s in enumerate([2, 2, 2, 1, 2, 2])])
    assert abs(r["credit"] - 11 / 12) < 1e-6            # old behaviour: 0.5
    assert judge.worst_window({**e, "aggregate": "min"}, [_res(i, s) for i, s in enumerate([2, 2, 2, 1, 2, 2])])["credit"] == 0.5


def test_negative_every_window_still_fails_on_one_window():
    e = {"id": "n1", "scale": 2, "every_window": True, "negative": True}
    rs = [{"id": "n1", "score": s, "found": s >= 1, "pass": s < 1} for s in [0, 0, 2, 0, 0, 0]]
    r = judge.worst_window(e, rs)
    assert r["pass"] is False and r["credit"] == 0.0
    assert judge.window_aggregate(e) == "min"


def test_binary_positive_every_window_stays_min():
    e = {"id": "b1", "every_window": True}
    rs = [{"id": "b1", "pass": p} for p in [True, True, False, True]]
    r = judge.worst_window(e, rs)
    assert r["pass"] is False and r["credit"] == 0.0


# ------------------------------------------------------------------ H4
class _R:
    def __init__(self, text, finish):
        self.response_text, self.finish_reason = text, finish
        self.completion_tokens, self.reasoning_tokens, self.reasoning_text = 1, 0, ""


class _JC:
    thinking = False
    context_length = 16384
    max_tokens = 4096
    row_timeout_s = None
    extra_body = {}
    sample_temperature = 0.7
    label = "fake"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, messages, **kw):
        self.calls.append(kw)
        return self.replies.pop(0)


def _row():
    return {"rubric": "steer", "case_id": "S", "prompt": "p", "response": "reply text here",
            "system": "s", "judge_elements": []}


GOOD = json.dumps({"obeyed": True, "note": ""})


def test_length_finish_is_retried_once_with_new_seed_and_half_cap():
    jc = _JC([_R("{\"obeyed\": tr", "length"), _R(GOOD, "stop")])
    out = judge.judge_row(jc, _row())
    assert out["judge_failed"] is False and out["scores"]["obeyed"] is True
    a, b = jc.calls
    assert b["seed"] != a["seed"] and b["temperature"] == judge.LENGTH_RETRY_TEMPERATURE
    assert b["max_tokens"] == 2048


def test_length_retry_is_bounded_then_voids():
    jc = _JC([_R("{", "length")] * 4)
    out = judge.judge_row(jc, _row())
    assert out["judge_failed"] is True and out["judge_finish_reason"] == "length"
    assert len(jc.calls) <= 3


# ------------------------------------------------------------ lint note
def test_term_density_warning_lists_terms_the_card_repeats():
    case = {"system": "The ward is cold. The ward hums. Keep the ward lit. A lantern.",
            "checks": [{"id": "td", "type": "term_density", "terms": ["ward", "lantern"]}]}
    w = lint.term_density_warnings(case)
    assert len(w) == 1 and "'ward' x3" in w[0] and "lantern" not in w[0]
    assert lint.term_density_warnings({"system": "x", "checks": []}) == []


# ------------------------------------------- CRAFT (cr1 every-window, cr2 sections)
def test_split_window_counts_once_and_cr2_sections_use_the_mean():
    e = {"id": "cr1", "scale": 2, "every_window": True}
    # window 0 was judged in two group calls (2, 0 -> median_low 0), window 1 once (2)
    rs = [{"id": "cr1", "score": 2, "_win": 0}, {"id": "cr1", "score": 0, "_win": 0},
          {"id": "cr1", "score": 2, "_win": 1}]
    assert judge.worst_window(e, rs)["credit"] == 0.5
    e2 = {"id": "cr2", "scale": 2, "sections": ["A", "B", "C", "D"]}
    rs2 = [{"id": "cr2", "score": s, "_win": i} for i, s in enumerate([2, 2, 1, 2])]
    out = judge._collapse_sections([e2], rs2)
    assert len(out) == 1 and abs(out[0]["credit"] - 7 / 8) < 1e-6        # old best-section: 1.0
    out = judge._collapse_sections([{**e2, "aggregate": "max"}], rs2)
    assert out[0]["credit"] == 1.0


# ------------------------------------------------------------ lint additions
def test_element_turn_range_and_unknown_keys():
    case = {"v2": True, "turns": ["u"] * 20, "judge_elements": [
        {"id": "cr1", "turns": [1, 18], "q": "x"},
        {"id": "k1", "q": "x", "quote_required_for_yes": True, "ftype": "format"}]}
    assert any("cr1" in p and "explicit list" in p for p in lint.wp8_field_problems(case))
    w = lint.element_key_warnings(case)
    assert len(w) == 1 and "quote_required_for_yes" in w[0] and "k1" in w[0]


# --------------------------------------------------------- H8 / H9 / H12
def test_bubbles_midnight_crossing_is_not_going_back():
    from crucibleforge.session_checks import check_bubbles
    tt = [(1, "[22:50] see you\n[23:41] sleep well"), (2, "[02:14] still up?"), (3, "[11:20] morning\n[11:25] hi")]
    ok, detail, ex = check_bubbles(tt, {})
    assert ok, detail
    bad = [(1, "[14:30] a"), (2, "[14:10] b")]
    assert not check_bubbles(bad, {})[0]


def test_positive_confirms_element_is_asked_when_flag_is_silent():
    row = {"check_results": [{"id": "fr", "flags": []}], "judge_elements": []}
    pos = {"id": "p1", "q": "present?", "confirms": "fr"}
    neg = {"id": "n1", "q": "drift?", "confirms": "fr", "negative": True}
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(judge, "_check_flags", lambda r, c: [])
    try:
        ask, auto = judge.confirm_split(row, [pos, neg])
    finally:
        mp.undo()
    assert [e["id"] for e in ask] == ["p1"] and [e["id"] for e in auto] == ["n1"]


def test_pipe_and_ellipsis_quotes_verify_each_part():
    text = "The brazier ticked in the cold. Later she set the cup down by the door and waited."
    assert judge.quote_found("The brazier ticked in the cold | she set the cup down by the door", text)
    assert judge.quote_found("The brazier ticked in the cold … she set the cup down by the door", text)
    assert not judge.quote_found("The brazier ticked in the cold | she never set anything anywhere", text)


def test_confirms_positive_lint_and_quote_turns_range():
    case = {"v2": True, "turns": ["u"] * 20, "judge_elements": [
        {"id": "p1", "q": "x", "confirms": "fr", "quote_turns": [3, 9]}]}
    assert any("p1" in w and "positive" in w for w in lint.element_key_warnings(case))
    assert any("quote_turns" in p for p in lint.wp8_field_problems(case))


# ------------------------------------------------------- GPT review 4 fixes
def test_negative_sections_use_the_worst_section_never_the_mean():
    e = {"id": "n2", "scale": 2, "negative": True, "sections": ["a", "b", "c"]}
    rs = [{"id": "n2", "score": sc, "found": sc >= 1, "pass": sc < 1, "_win": i}
          for i, sc in enumerate([0, 2, 0])]
    for el in (e, {**e, "aggregate": "mean"}):
        out = judge._collapse_sections([el], rs)
        assert len(out) == 1 and out[0]["pass"] is False and out[0]["credit"] == 0.0
    clean = [{**r, "score": 0, "found": False, "pass": True} for r in rs]
    assert judge._collapse_sections([e], clean)[0]["credit"] == 1.0


def test_tiered_threshold_edges():
    from crucibleforge.session_checks import _tiered
    chk = {"t1": 10, "t2": 20}
    cr = lambda v: _tiered(v, chk, 1, 2, "u")[2]["credit"]  # noqa: E731
    assert [cr(10), cr(10.01), cr(20), cr(20.01)] == [1.0, 0.5, 0.5, 0.0]


def test_bubbles_wrap_sequences():
    from crucibleforge.session_checks import check_bubbles
    assert check_bubbles([(1, "[23:50] a"), (2, "[00:05] b")], {})[0]
    assert check_bubbles([(1, "[11:59] a"), (2, "[12:00] b")], {})[0]
    assert check_bubbles([(1, "[11:50 pm] a"), (2, "[12:05 am] b")], {})[0]
    assert not check_bubbles([(1, "[23:50] a"), (2, "[00:05] b"), (3, "[00:01] c")], {})[0]
    assert not check_bubbles([(1, "[12:00] a"), (2, "[11:59] b")], {})[0]


def test_lint_accepts_dim_and_peak_element_keys():
    case = {"v2": True, "turns": ["u"] * 3, "judge_elements": [
        {"id": "h1", "q": "x", "dim": "explicitness", "peak": True}]}
    assert lint.element_key_warnings(case) == []


def test_multipart_quote_parts_must_be_in_order():
    text = "The brazier ticked in the cold. Later she set the cup down by the door and waited."
    assert judge.quote_found("The brazier ticked in the cold | she set the cup down by the door", text)
    assert not judge.quote_found("she set the cup down by the door | The brazier ticked in the cold", text)
