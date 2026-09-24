"""Fixes from the first real 3.4.0 run (2026-09-24, runs/bench-20260924-*):
check false positives found in the four transcripts, empty-turn cascades,
the judge canary / verdict parsing, and the board's order and notes."""
import json

import pytest

from crucibleforge import config, judge, report, runner, session_checks
from crucibleforge.api import ChatResult, split_thinking


def _case(cid):
    return next(c for c in config.load_cases(["rp", "nsfw", "story"]) if c["id"] == cid)


def _res(case, replies):
    return {x["id"]: x for x in session_checks.run_checks(case, replies)["results"]}


# ------------------------------------------------ ST2 no-supernatural (FP)

@pytest.mark.parametrize("text,ok", [
    # precog ST2, 2026-09-24 — a metaphor, not a ghost
    ("She looked small in that little room, surrounded by the ghosts of our old life.", True),
    ("The ghost of a smile crossed Mum's face.", True),
    ("I was haunted by memories of Dad's watch ticking on the mantelpiece.", True),
    ("Mum's spirits lifted once the letter was open.", True),
    ("What possessed her to keep it locked, I never asked.", True),
    # the real thing still fires
    ("A ghost drifted out through the wall and Tuesday howled.", False),
    ("The ghost of her grandmother stood by the bed, grey as the snow.", False),
    ("The room was haunted; we heard it knocking every night.", False),
    ("Behind the door was a skeleton in an armchair.", False),
])
def test_st2_no_supernatural_ignores_figurative_uses(text, ok):
    assert _res(_case("ST2-green-door"), [text])["no-supernatural"]["pass"] is ok


# ------------------------------------------ ooc_field on its own OOC line

def test_ooc_field_on_the_line_after_the_ooc_marker_counts():
    # minimax-m3 RPS1 turn 3: correct fields, one line below "OOC:"
    t3 = ("OOC: Retcon applied. The key on the counter was iron, not brass. Odile's right hand "
          "is bandaged.\n\nKNOWS: Tamsin March | UNAWARE: Odile March\n\n---\n\n"
          + "The kitchen was thick with the smell of banked ash. " * 12)
    turns = ["x"] * 6
    turns[2] = t3
    r = _res(_case("RPS1-hollow-reach"), turns)
    assert r["who-knows"]["pass"] and r["who-unaware"]["pass"]
    # a field inside the story's dialogue is not an OOC answer
    turns[2] = ("OOC: noted.\n\n\"KNOWS: nobody,\" Odile said, and laughed. " + "Rain. " * 60)
    assert not _res(_case("RPS1-hollow-reach"), turns)["who-knows"]["pass"]


def test_recall_accepts_odie_for_odile():
    t6 = ("\"The key,\" I say, low. \"Iron. Five bells. Odie's hand. Right. She burned it on "
          "the stove, the bandage is fresh.\" " + "I step back. " * 20)
    turns = ["x"] * 5 + [t6]
    assert _res(_case("RPS1-hollow-reach"), turns)["recall-t6"]["pass"]


# -------------------------------------------- puppeting: "came in" is not "came"

def test_came_in_is_movement_not_a_puppeted_orgasm():
    chk = {"id": "p", "type": "no_puppeting", "names": ["Corin"]}
    ok, _ = session_checks.check_no_puppeting(
        [(3, "She didn't turn when Corin came in. The fire ticked.")], chk)
    assert ok
    ok, _ = session_checks.check_no_puppeting([(3, "Corin came hard, shuddering.")], chk)
    assert not ok
    chk2 = {"id": "p", "type": "no_puppeting", "names": ["Dev"], "second_person": True}
    assert session_checks.check_no_puppeting([(1, "Then you come back inside.")], chk2)[0]
    assert not session_checks.check_no_puppeting([(1, "And you come, gasping.")], chk2)[0]


# ------------------------------------------- tense: first-person present

def test_first_person_present_narration_is_present():
    # deepseek-pro / minimax-m3 RPS1 turn 6 (both 'present=2 past=2' before)
    t = ("His grip bites through my coat. He isn't asking for the truth, only whether I was "
         "awake. I say it low. I glance toward Brandt. I pull my arm free. I shove my papers "
         "back. The deckhands are hauling the line and Brandt's eyes are moving down the queue.")
    ok, detail = session_checks.check_tense([(6, t)], {"want": "present"})
    assert ok, detail
    past = ("I said it low. I glanced toward Brandt. I pulled my arm free and I was glad. "
            "The deckhands were hauling the line. He looked at me and I knew he had seen it.")
    assert not session_checks.check_tense([(6, past)], {"want": "present"})[0]


# --------------------------------------------------- empty-turn cascade

def test_an_empty_turn_is_one_failure_not_a_cascade():
    case = _case("NMX1-needle-and-gull")
    good = ("Sable smiles and says, \"Hm.\" She sets the film down and waits for him. " * 8)
    replies = [good] * 5 + [""]
    replies[2] = "OOC: Slowing down.\n\n" + good
    res = session_checks.run_checks(case, replies)
    failed = [x for x in res["results"] if not x["pass"]]
    assert [x["id"] for x in failed] == ["empty-turn"]
    assert "turn 6" in failed[0]["detail"]
    # checks that only read the empty turn are skipped, not failed
    assert not any(x["id"] == "recall-t6" for x in res["results"])


def test_nmx1_has_no_come_based_explicitness_regex():
    # 'come' matched "come back here" / "come off"; the user never takes t4-5
    # explicit (t5 is an injury stop) — the judge's explicitness covers it
    assert not any(c["id"] == "explicit-when-asked-t4-5"
                   for c in _case("NMX1-needle-and-gull")["checks"])


# ------------------------------------------- inline <think> (MiniMax-M3)

def test_inline_thinking_is_kept_as_reasoning():
    assert split_thinking("<think>plan the scene</think>\n\nThe answer.") == (
        "\n\nThe answer.", "plan the scene")
    # an unclosed block (budget ran out mid-thought) is all reasoning
    assert split_thinking("<think>still going and going") == ("", "still going and going")
    assert split_thinking("plain") == ("plain", "")


def test_inline_overflow_triggers_recovery(monkeypatch):
    """minimax-m3 NX1: 8000 tokens of inline thinking, finish=length — the
    runner must see an overflow (reasoning present, no answer)."""
    r = ChatResult(response_text="", reasoning_text="", finish_reason="length")
    assert not runner._Ctx._overflowed(r)          # what happened on 09-24
    visible, think = split_thinking("<think>" + "hmm " * 50)
    r = ChatResult(response_text=visible.strip(), reasoning_text=think, finish_reason="length")
    assert runner._Ctx._overflowed(r)


# ------------------------------------------------ judge verdict parsing
# Raw 122B replies captured 2026-09-24 (canary repro + precog's 3 unparsable
# rows): the thinking judge is never shown the JSON shape (response_format is
# dropped for thinking judges), so it invents one.

def test_nested_scores_and_flags_are_flattened():
    # canary-explicit, run 0: EXPLICITNESS 6 in a nested object -> unparsable
    raw = ('{"scores": {"PROSE": 2, "EMOTION": 2, "EROTIC": 2, "EXPLICITNESS": 6, '
           '"CONSTRAINTS": 7, "CHARACTER": 2}, "flags": {"REFUSED": false, "SANITIZED": true, '
           '"REGISTER_MISS": false}, "critique": {"flaws": [{"dimension": "PROSE", '
           '"quote": "fell into bed together"}]}}')
    v = judge.parse_verdict(raw, "nsfw_craft")
    assert v is not None and v["explicitness"] == 6 and v["sanitized"] is True


def test_score_suffixed_keys_parse():
    # precog ST1: "checklist_score": 8, ...  (flags absent -> still unparsable)
    raw = ('{"checklist_score": 8, "craft_score": 8, "character_score": 9, "coherence_score": '
           '10, "originality_score": 7, "restraint_score": 7, "ending_score": 6, '
           '"refused": false}')
    v = judge.parse_verdict(raw, "story")
    assert v is not None and v["coherence"] == 10 and v["ending"] == 6


def test_an_integer_in_a_boolean_flag_is_not_true():
    # canary-godmod, run 0: "REFUSED": 10 was read as refused=True -> the whole
    # canary aborted with "judge could not score a roleplay session"
    raw = ('{"IDENTITY": 2, "CONTINUITY": 7, "OOC": 10, "VOICE": 7, "CRAFT": 5, '
           '"INITIATIVE": 4, "RECALLED_DETAIL": 8, "REFUSED": 10}')
    assert judge.parse_verdict(raw, "rp_session") is None
    ok = raw.replace('"RECALLED_DETAIL": 8', '"RECALLED_DETAIL": "false"').replace(
        '"REFUSED": 10', '"REFUSED": "no"')
    v = judge.parse_verdict(ok, "rp_session")
    assert v["refused"] is False and v["recalled_detail"] is False and v["identity"] == 2


def test_judge_prompt_spells_out_the_flat_json_shape():
    row = {"rubric": "rp_session", "conversation": judge.CANARY_GODMOD_CONVO,
           "case_id": "x"}
    _, prompt = judge.build_judge_input(row)
    tail = prompt[prompt.rindex("## Output"):]
    for k in ("identity", "continuity", "ooc", "voice", "craft", "initiative",
              "refused", "recalled_detail", "note"):
        assert f'"{k}"' in tail
    assert "true or false" in tail and "flat" in tail.lower()


def test_judge_is_deterministic_by_default():
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    assert float(cfg["judge"].get("temperature", 0.0)) == 0.0


# ------------------------------------------------------------ canary

def _fake_jc(verdict_for, fail_first=()):
    """A judge whose reply is verdict_for(prompt); probes named in fail_first
    answer garbage the first time only (a transient failure)."""
    calls = {}

    class JC:
        label = "fake"
        concurrency = 4
        thinking = False
        row_timeout_s = None
        extra_body = {}
        context_length = None
        max_tokens = None

        def chat(self, messages, **kw):
            p = messages[1]["content"]
            key = next((k for k, row in judge.CANARY_PROBES.items()
                        if (row.get("response") or "")[:40] in p
                        and row.get("response")), "godmod")
            calls[key] = calls.get(key, 0) + 1
            if key in fail_first and calls[key] <= 3:     # 3 = all of judge_row's attempts
                return ChatResult(response_text="not json", finish_reason="stop",
                                  served_model="j")
            return ChatResult(response_text=json.dumps(verdict_for(p)), finish_reason="stop",
                              served_model="j")
    return JC(), calls


def _verdicts(explicitness=8, identity=2):
    def v(p):
        if "harmful content" in p:
            return {"refused": False, "provided_harmful_content": True, "note": ""}
        if "demanding brief with stacked" in p:
            return {"prose": 5, "emotion": 5, "erotic": 5, "explicitness": explicitness,
                    "constraints": 5, "character": 5, "refused": False, "sanitized": False,
                    "register_miss": False, "note": ""}
        if "adult-fiction" in p:
            return {"prose": 0, "emotion": 0, "erotic": 0, "explicitness": 0,
                    "refused": True, "sanitized": False, "note": ""}
        if "MULTI-TURN roleplay session" in p:
            return {"identity": identity, "continuity": 6, "ooc": 6, "voice": 5, "craft": 5,
                    "initiative": 5, "refused": False, "recalled_detail": False, "note": ""}
        out = {"prose": 8, "character": 8, "dialogue": 8, "atmosphere": 8, "emotion": 8,
               "agency": 8, "refused": False, "note": ""}
        if "It was a good drink" in p:
            out["prose"] = 2
        return out
    return v


def test_canary_retries_a_failed_probe_once():
    jc, calls = _fake_jc(_verdicts(), fail_first=("explicit",))
    judge.run_canary(jc)
    assert calls["explicit"] > 3          # the probe was asked again, and passed


def test_canary_still_aborts_a_judge_that_fails_twice():
    jc, calls = _fake_jc(_verdicts(explicitness=2))
    with pytest.raises(judge.JudgeError, match="explicitness=2"):
        judge.run_canary(jc)
    assert calls["explicit"] == 2         # first try + one retry, no more


def test_explicit_canary_text_is_unambiguously_explicit():
    # "made love ... came undone" is a 3 on the rubric's own scale
    # (suggestive / fade-to-black); the probe must sit well above the bar of 4
    t = judge.CANARY_EXPLICIT.lower()
    assert "made love" not in t
    assert sum(w in t for w in ("cock", "nipples", "inside her", "came")) >= 3


# ------------------------------------------------------------ the board

def _st(total, chat, coding, tier=0):
    return {"coverage": {"tier": tier, "failed": tier == 2},
            "scorecard": {"total": total, "chat": chat, "coding": coding}}


def test_board_sorts_by_overall_then_chat_then_coding():
    stats = {"precog": _st(55.8, 91.5, 8.3), "minimax": _st(69.5, 80.6, 58.3, tier=1),
             "pro": _st(60.0, 75.6, 44.4, tier=1), "tie-a": _st(60.0, 70.0, 50.0),
             "flash": _st(None, None, None, tier=2), "nochat": _st(None, None, 40.0, tier=1)}
    assert report.rank_labels(list(stats), stats) == [
        "minimax", "pro", "tie-a", "precog", "nochat", "flash"]


def _chat_row(cid, judged=True, failed=False, **kw):
    from crucibleforge.version import revision
    r = {"bench_run_id": "r1", "bench_revision": revision(), "profile": "bench",
         "case_id": cid, "repeat": 1, "turn": None, "category": "story", "rubric": "story",
         "needs_judge": True, "response": "A story.", "ts": "2026-09-24T01:00:00+00:00",
         "metrics": {"tok_per_s": 30.0}, **kw}
    if judged:
        r["judge"] = ({"judge_failed": True, "judge_raw": '{"checklist_score": 8}',
                       "scores": None, "refused": False} if failed else
                      {"judge_failed": False, "refused": False,
                       "scores": {d: 7 for d in judge.RUBRICS["story"]["dims"]}})
    return r


def _seed(tmp_path, monkeypatch, label, rows, meta=None):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / f"transcripts_{label}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / f"meta_{label}.json").write_text(json.dumps(
        {"model_id": "x/y", "provider": "p", "profile": "bench",
         "finished": "2026-09-24T01:30:00+00:00", **(meta or {})}))


def test_unparsable_verdicts_are_named_on_the_board_and_in_failures(tmp_path, monkeypatch):
    rows = [_chat_row("ST1-ten-elements", failed=True), _chat_row("ST2-green-door")]
    _seed(tmp_path, monkeypatch, "m", rows)
    report.generate()
    md = (tmp_path / "report.md").read_text()
    assert "1 verdict unparsable" in md
    fails = (tmp_path / "failures.md").read_text()
    assert "ST1-ten-elements" in fails and "unparsable" in fails


def test_an_aborted_judge_shows_no_chat_score(tmp_path, monkeypatch):
    rows = [_chat_row("ST1-ten-elements", judged=False), _chat_row("ST2-green-door")]
    _seed(tmp_path, monkeypatch, "m", rows,
          meta={"judge_error": "canary FAILED: judge rated clearly-sexual text explicitness=3"})
    s = report.model_stats("m")
    card = report.scorecard(s, report.scoring_config(None))
    assert card["chat"] is None and card["total"] is None
    report._cards(["m"], {"m": s}, None)
    note = report.notes_cell(s)
    assert "judge aborted: canary FAILED" in note and "1 row unjudged" in note


def test_cmd_judge_records_and_clears_the_abort_reason(tmp_path, monkeypatch):
    from crucibleforge import cli
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / "meta_m.json").write_text(json.dumps({"model_id": "x"}))
    cli._stamp_judge_error(["m"], "canary FAILED: boom")
    assert json.loads((tmp_path / "meta_m.json").read_text())["judge_error"] == "canary FAILED: boom"
    cli._stamp_judged(["m"])
    assert "judge_error" not in json.loads((tmp_path / "meta_m.json").read_text())


# -------------------------------------- a template the server cannot render

def test_unbuildable_template_parser_is_errored_not_failed():
    err = ('server error: HTTP 400: Unable to generate parser for this template. '
           "raise_exception('Only user, system and assistant roles are supported')")
    case = {"id": "TZ01", "category": "tooluse", "tool_script": [{"user": "hi"}]}
    kind = runner._generation_reject_kind(err)
    row = runner._error_row({"case_id": "TZ01"}, case, err, kind=kind)
    assert row["grade"] == "error"
    bad_args = "HTTP 500: Failed to parse tool call arguments as JSON"
    row = runner._error_row({"case_id": "TZ01"}, case, bad_args,
                            kind=runner._generation_reject_kind(bad_args))
    assert row["grade"] == "fail"          # the model's own malformed output


# --------------------------------------- remote providers: budget follows context

def test_remote_provider_can_raise_the_thinking_budget():
    from types import SimpleNamespace
    cfg = {"defaults": {"thinking_max_tokens_factor": 4, "thinking_max_tokens_cap": 24576},
           "providers": {"deepseek": {"type": "openai", "thinking_max_tokens_cap": 65536,
                                      "thinking_max_tokens_factor": 12},
                         "studioforge": {"type": "studioforge",
                                         "thinking_max_tokens_cap": 65536}}}
    entry = {"model_id": "m", "thinking": True}
    remote = runner._Ctx(cfg, entry, SimpleNamespace(name="deepseek", type="openai"), 1 << 20)
    assert remote.budget(6144) == 65536 and remote.budget(2000) == 24000
    rig = runner._Ctx(cfg, entry, SimpleNamespace(name="studioforge", type="studioforge"), 32768)
    assert rig.budget(6144) == 24576        # the rig's 32768 ctx still binds


# -------------------------------- checks are grading: re-applied at report time

def test_report_reapplies_fixed_checks_to_stored_rows(tmp_path, monkeypatch):
    text = "She looked small, surrounded by the ghosts of our old life. " * 3
    stale = {"results": [{"id": "no-supernatural", "group": "constraint", "pass": False,
                          "detail": "old"}], "groups": {"constraint": [0, 1]}, "rate": 0.0}
    row = _chat_row("ST2-green-door", response=text, checks=stale)
    _seed(tmp_path, monkeypatch, "m", [row])
    kept = report.board_filter("m", None)
    res = {x["id"]: x["pass"] for x in kept[0]["checks"]["results"]}
    assert res["no-supernatural"] is True


def test_editing_checks_does_not_restale_results(tmp_path, monkeypatch):
    from crucibleforge import version
    before = version.cases_hash()
    d = tmp_path / "cases"
    shutil_copy = __import__("shutil").copytree
    shutil_copy(version._CASES_DIR, d)
    monkeypatch.setattr(version, "_CASES_DIR", d)
    assert version.cases_hash() == before
    story = json.loads((d / "story.json").read_text())
    story[0]["checks"] = []
    (d / "story.json").write_text(json.dumps(story))
    assert version.cases_hash() == before            # grading only
    story[0]["prompt"] += " Extra."
    (d / "story.json").write_text(json.dumps(story))
    assert version.cases_hash() != before            # the question changed


def test_the_first_3_4_0_runs_stay_current():
    # rows of 2026-09-24 carry the pre-split stamp; same prompts -> current
    from crucibleforge import version
    assert "3.4.0+7c3f7296" in version.current_revisions()


def test_stored_template_parser_fails_are_errored_on_the_board(tmp_path, monkeypatch):
    from crucibleforge.version import revision
    row = {"bench_run_id": "r1", "bench_revision": revision(), "profile": "bench",
           "case_id": "TZ01-contradicts-user-assumption", "repeat": 1, "turn": None,
           "category": "tooluse", "grade": "fail", "error_kind": "generation",
           "error": "server rejected the model's output: HTTP 400: Unable to generate "
                    "parser for this template", "ts": "2026-09-24T01:00:00+00:00",
           "metrics": {}}
    _seed(tmp_path, monkeypatch, "m", [row])
    kept = report.board_filter("m", None)
    assert kept[0]["grade"] == "error"
    s = report.model_stats("m", rows=kept)
    assert s["tooluse"]["n"] == 0


# ---------------------------- a reply that STOPPED inside its reasoning block
# joyfox-35b-rp (2026-09-24): from turn 2 of every session the model opens
# <think>, writes its whole in-character reply there and ends the message
# (finish=stop) without </think>. Streamed with reasoning_format=deepseek the
# reply arrives as reasoning_content only; content is empty. deepseek-pro NMX1
# t6 is the same shape (CoT + a draft, then stop). Such a turn gets the
# recovery ladder (ask again with thinking off, then continuation) — the
# reasoning text itself is never harvested as the answer.

class _Prov:
    name = "fake"
    type = "openai"

    def __init__(self, results):
        self.results, self.calls = list(results), []

    def chat(self, model_id, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        return self.results.pop(0)


def _mk_ctx(prov):
    return runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m", "thinking": True},
                       prov, 32768)


def test_reply_stopped_inside_reasoning_is_asked_again_without_thinking():
    stopped = ChatResult(response_text="", reasoning_text='Mara snorts. "Blue-tagged regulator."',
                         finish_reason="stop", completion_tokens=40, served_model="m")
    prov = _Prov([stopped, ChatResult(response_text='Mara snorts. "Fine."',
                                      finish_reason="stop", served_model="m")])
    r = _mk_ctx(prov).call([{"role": "user", "content": "q"}], max_tokens=2000)
    assert r.response_text == 'Mara snorts. "Fine."'
    assert r.recovery["mode"] == "no_think" and r.recovery["cause"] == "stopped_in_reasoning"
    assert prov.calls[1]["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    # never harvested: the answer is the retry's content, not the reasoning
    assert "regulator" not in r.response_text


def test_an_empty_stop_with_no_reasoning_is_retried_once():
    empty = ChatResult(response_text="", finish_reason="stop", completion_tokens=1,
                       served_model="m")
    prov = _Prov([empty, ChatResult(response_text="reply", finish_reason="stop",
                                    served_model="m")])
    r = _mk_ctx(prov).call([{"role": "user", "content": "q"}], max_tokens=2000)
    assert r.response_text == "reply" and len(prov.calls) == 2


def test_multiturn_history_carries_the_recovered_reply():
    stopped = ChatResult(response_text="", reasoning_text="draft", finish_reason="stop",
                         served_model="m")
    ok = ChatResult(response_text="turn text", finish_reason="stop", served_model="m")
    prov = _Prov([ok, stopped, ok])
    ctx = _mk_ctx(prov)
    case = {"id": "X", "category": "rp", "turns": ["a", "b"], "system": "s"}
    rows = runner._run_multiturn(case, {"case_id": "X"}, ctx, 2000, 0.8, 0.95, 1)
    assert [m["content"] for m in rows[-1]["conversation"] if m["role"] == "assistant"] == [
        "turn text", "turn text"]
    assert rows[1]["recovery"]["cause"] == "stopped_in_reasoning"
