"""4.5 (lead 2026-11-14): in a SESSION, a reply the degenerate test flags (a visible loop, or
an empty reply whose reasoning loops) is recorded on its row as it happened, stamped
``degenerate``, counts as a failure for that turn and goes into history as an EMPTY turn,
exactly as ``empty_stop`` does -- the loop is never fed back as the model's own reply (the
same contagion path as the cut fragment). Never regenerated (K's rule); the session goes on.
A single-item row is unchanged."""
from __future__ import annotations

from crucibleforge import runner, session_checks
from crucibleforge.api import ChatResult

LOOP = "Odile Odile Odile " * 150


class _P:
    name, type = "fake", "openai"
    extra_body, reasoning_replay = {}, False

    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    def chat(self, model_id, messages, **kw):
        self.sent.append([dict(m) for m in messages])
        c, r, f = self.replies.pop(0)
        return ChatResult(response_text=c, reasoning_text=r, finish_reason=f, completion_tokens=50,
                          prompt_tokens=100, served_model="m")

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(p, rule=True):
    ctx = runner._Ctx({"defaults": {"empty_stop_rule": rule, "on_empty_stop": "regenerate"}},
                      {"name": "m", "model_id": "m", "thinking": True}, p, 32768)
    ctx.recovery_enabled = False
    return ctx


def _session(replies, monkeypatch, rule=True):
    seen = {}

    def run_checks(case, replies):
        seen["replies"] = list(replies)
        return {"results": [{"id": f"t{i+1}", "pass": bool(r.strip())} for i, r in enumerate(replies)],
                "rate": sum(bool(r.strip()) for r in replies) / len(replies)}

    monkeypatch.setattr(session_checks, "run_checks", run_checks)
    p = _P(replies)
    rows = runner._run_multiturn({"id": "S", "category": "rpdeep", "turns": ["a", "b", "c", "d"],
                                  "max_tokens": 64, "rubric": "rp_multi"}, {}, _ctx(p, rule),
                                 64, 0.6, 0.95, 1)
    return p, rows, seen


def test_a_visible_loop_on_turn_3_is_stamped_fails_and_goes_into_history_empty(monkeypatch):
    p, rows, seen = _session([("One.", "", "stop"), ("Two.", "", "stop"), (LOOP, "", "stop"),
                              ("Four.", "", "stop")], monkeypatch)
    r3 = rows[2]
    assert r3["response"] == LOOP                                  # recorded as it happened
    assert r3["degenerate"]["channel"] == "answer" and r3["degenerate"]["history"] == "empty"
    assert len(p.sent) == 4                                       # never regenerated
    assert seen["replies"][2] == "" and rows[-1]["checks"]["results"][2]["pass"] is False
    sent4 = p.sent[3]
    assert not any("Odile Odile" in (m.get("content") or "") for m in sent4)
    assert rows[-1]["conversation"][5] == {"role": "assistant", "content": ""}
    assert rows[-1]["checks"]["results"][3]["pass"] is True        # the session goes on


def test_an_empty_reply_with_looping_reasoning_is_empty_in_history_and_not_regenerated(monkeypatch):
    p, rows, seen = _session([("One.", "", "stop"), ("", LOOP, "stop"), ("Three.", "", "stop"),
                              ("Four.", "", "stop")], monkeypatch)
    r2 = rows[1]
    assert r2["degenerate"]["channel"] == "reasoning" and "empty_stop" in r2
    assert len(p.sent) == 4 and seen["replies"][1] == ""


def test_without_the_rule_the_session_history_is_unchanged(monkeypatch):
    p, rows, seen = _session([("One.", "", "stop"), ("Two.", "", "stop"), (LOOP, "", "stop"),
                              ("Four.", "", "stop")], monkeypatch, rule=False)
    assert seen["replies"][2] == LOOP and "degenerate" not in rows[2]


def test_a_single_item_row_is_unchanged():
    p = _P([(LOOP, "", "stop")])
    row = runner._run_single({"id": "c", "category": "general", "prompt": "q", "max_tokens": 64},
                             {}, _ctx(p), 64, 0.6, 0.95, 1)
    assert row["response"] == LOOP and "history" not in (row.get("degenerate") or {})
    assert len(p.sent) == 1
