"""W4 half: on NSFW+ a provider filter stop is 0 and counted 'filtered' (never a retry or 'missing'). The Code/Chat
half (filter = missing and re-run) belongs to the lane that wires the profile."""
from crucibleforge import classing45 as C


def test_filter_stop_scores_zero_and_is_counted():
    g = C.gate_result(None, finish_reason="content_filter")
    assert g["explicit"] == 0 and g["reason"] == "filtered"
    assert C.counts45([g])["filtered"] == 1


def test_filter_beats_empty_and_refusal_order():
    g = C.gate_result({"cls": "REFUSED"}, finish_reason="content_filter", empty=True, refusal_phrase=True)
    assert g["reason"] == "filtered"


def test_unit_with_filtered_reply_is_not_sent_to_readers():
    assert C.pre_read_reason(["x"], ["content_filter"]) == "filtered"
    assert C.pre_read_reason([""], ["stop"]) == "empty"
    assert C.pre_read_reason(["x"], ["length"]) == "cut"
    assert C.pre_read_reason(["x"], ["stop"]) is None


# ---------------------------------------------------------------- the Code / Chat half (lead decision 2026-11-19)
def _blocked(cid, category, chain_part=None):
    r = {"case_id": cid, "repeat": 1, "turn": None, "category": category, "grade": "pass", "response": "",
         "finish_reason": "content_filter"}
    if chain_part:
        r["chain"] = {"part": chain_part}
    return r


def test_filter_stop_on_code_and_chat_is_missing_and_rerun_never_zero():
    from crucibleforge import report, scoring45
    code, chat = _blocked("CODE1", "coding", "work/c1_implied"), _blocked("CHAT1", "rpdeep", "chat/j_rplong")
    nsfw = _blocked("N1", "nsfw", "nsfw/n7_taboo")
    for r in (code, chat):
        assert scoring45.job_missing_reason([r]) == "content_filter"
    assert scoring45.job_missing_reason([nsfw]) is None                       # NSFW+: scored (0, counted)
    kept, listed = scoring45.split_missing({1: [code, chat, nsfw]})
    assert [r["case_id"] for r in kept[1]] == ["N1"]                          # missing rows are not scored 0
    assert {(x["case_id"], x["reason"]) for x in listed} == {("CODE1", "content_filter"), ("CHAT1", "content_filter")}
    why = report._held_part_why({"CODE1"}, {"CODE1"}, {"CODE1": [x for x in listed if x["case_id"] == "CODE1"]})
    assert why == "a re-run of 1 missing job"                                 # the board asks for a re-run
