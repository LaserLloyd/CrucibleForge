"""Review E (2026-11-25, owner: a check never withholds a score; a model that cannot do a case scores zero on it).

1. A server's "the prompt does not fit the context" answer (llama-server sends it as HTTP 500) is the existing
   ``ctx_short`` outcome for that job, never a transport failure, and never counts toward the consecutive-failure
   abort (one model lost its whole row to three of them in a row).
2. A server's rejection of the model's OWN output ("The model produced output that does not match the expected
   <x> format") is retried like a transport error; once the retries are used up the reply is a scored miss
   (finish ``model_output_invalid``, graded as an empty answer by the case's grader), counted in the row's notes,
   never missing, never the abort.
3. A server that is really gone still aborts the model.
"""
from __future__ import annotations

import pytest

from crucibleforge import api, attempts45, config, providers, report, runner, scoring45
from crucibleforge.api import ChatResult, ModelOutputInvalid, TransportError

PEG = '{"code": 500, "message": "The model produced output that does not match the expected peg-native format", ' \
      '"type": "server_error"}'
FIT = ('{"message": "The prompt does not fit the context \'vendor/m\' is loaded with (79872 tokens per slot). '
       'Shorten the prompt, or load this model at a larger ctx_size"}')


# ------------------------------------------------------------------ classification

def test_a_model_output_rejection_is_its_own_class_and_a_plain_5xx_is_not():
    assert isinstance(api.classify_server_error(500, PEG), ModelOutputInvalid)
    for body in ("internal error", '{"message": "Service temporarily unavailable."}', FIT):
        e = api.classify_server_error(500, body)
        assert isinstance(e, TransportError) and not isinstance(e, ModelOutputInvalid), body
    assert api.model_output_invalid("Failed to parse the model output at pos 12")


def test_a_model_output_rejection_is_retried_then_raised(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise api.classify_server_error(500, PEG)

    monkeypatch.setattr(api, "stream_chat", boom)
    monkeypatch.setattr(api, "RETRY_BACKOFF_S", 0.0)
    with pytest.raises(ModelOutputInvalid):
        api.stream_chat_retried("http://x", "", "m", [], max_tokens=8)
    assert len(calls) == api.MAX_TRANSPORT_RETRIES


# ------------------------------------------------------------------ the run

def _answer(text="ok"):
    return ChatResult(response_text=text, finish_reason="stop", prompt_tokens=5, completion_tokens=2)


def _run(tmp_path, monkeypatch, chat, n=6):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    cfg = {"defaults": {"context_length": 4096, "repeats": {}},
           "providers": {"fake": {"type": "openai", "base_url": "http://127.0.0.1:9/v1", "concurrency": 1}},
           "judge": {"candidates": []}, "models": []}
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": False}
    monkeypatch.setattr(providers.Provider, "chat", chat)
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"m"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    cases = [{"id": f"I{i}", "category": "instruct", "prompt": f"x{i}", "max_tokens": 8,
              "grader": "contains", "grader_config": {"needles": ["ok"]}} for i in range(n)]
    summary = runner.run_models(cfg, [entry], cases)
    return summary, {r["case_id"]: r for r in config.load_transcripts("m")}


def test_unparsable_model_output_is_a_scored_miss_and_never_aborts(tmp_path, monkeypatch):
    def chat(self, model_id, messages, **kw):
        if messages[-1]["content"] in ("x0", "x1", "x2", "x3"):          # four in a row: more than the abort count
            raise ModelOutputInvalid(f"server error: {PEG}")
        return _answer()

    summary, rows = _run(tmp_path, monkeypatch, chat)
    assert summary["m"]["failed"] is False
    for cid in ("I0", "I1", "I2", "I3"):
        r = rows[cid]
        assert r["finish_reason"] == runner.MODEL_OUTPUT_INVALID and "peg-native" in r["model_output_invalid"]
        assert r["grade"] == "fail" and not r.get("error")                 # graded by its grader, not errored
        assert scoring45.job_missing_reason([r]) is None                   # scored, never missing
        assert not attempts45.is_infra(r)                                  # a resume never re-runs it
    assert rows["I4"]["grade"] == "pass" and rows["I5"]["grade"] == "pass"


def test_the_prompt_not_fitting_is_ctx_short_and_never_aborts(tmp_path, monkeypatch):
    def chat(self, model_id, messages, **kw):
        if messages[-1]["content"] in ("x0", "x1", "x2", "x3"):
            raise api.classify_server_error(500, FIT)
        return _answer()

    summary, rows = _run(tmp_path, monkeypatch, chat)
    assert summary["m"]["failed"] is False
    for cid in ("I0", "I1", "I2", "I3"):
        r = rows[cid]
        assert r["error_kind"] == "ctx_short" and scoring45.job_missing_reason([r]) == "ctx_short"
    assert rows["I5"]["grade"] == "pass"


def test_an_unreachable_server_still_aborts(tmp_path, monkeypatch):
    def chat(self, model_id, messages, **kw):
        raise TransportError("connection refused")

    summary, rows = _run(tmp_path, monkeypatch, chat)
    assert summary["m"]["failed"] is True and "consecutive" in summary["m"]["error"]
    assert len(rows) == runner.MAX_CONSECUTIVE_TRANSPORT_ERRORS
    assert all(r["error_kind"] == "transport" for r in rows.values())


def test_a_plain_5xx_is_still_a_transport_failure(tmp_path, monkeypatch):
    def chat(self, model_id, messages, **kw):
        raise api.classify_server_error(500, '{"message": "Service temporarily unavailable."}')

    summary, rows = _run(tmp_path, monkeypatch, chat)
    assert summary["m"]["failed"] is True
    assert all(r["error_kind"] == "transport" and r["grade"] == "error" for r in rows.values())


# ------------------------------------------------------------------ the board says it

def test_the_row_notes_count_the_unparsable_replies():
    assert report.model_output_invalid_note(1) == "1 reply could not be parsed by the server and count as misses"
    s = {"model_output_invalid": 3, "coverage": {}}
    assert "3 replies could not be parsed by the server and count as misses" in report.notes_cell(s)
    assert "3 replies could not be parsed by the server and count as misses" in report._model_notes("m", s)


def test_only_a_context_refusal_takes_the_ctx_short_path():
    fit = api.classify_server_error(500, FIT)
    assert runner._context_refusal_only(fit)
    assert not runner._context_refusal_only(api.classify_server_error(500, "internal error"))
    assert not runner._context_refusal_only(TransportError("connection refused"))
    assert not runner._context_refusal_only(api.ProtocolRejected(400, "does not fit the context"))


def test_the_sanity_guard_does_not_read_unparsable_replies_as_a_silent_model():
    st = {"sanity_total": 0, "sanity_empty": 0}
    rows = [{"turn": 1, "response": "", "finish_reason": runner.MODEL_OUTPUT_INVALID}] * 5
    runner._sanity_check(st, rows, "m")
    assert st["sanity_total"] == 0
