"""``crucibleforge conform`` — the per-model protocol checks (2026-11-02).

The bench used to ASSUME one request protocol for every model (root cause
R1).  When that assumption is wrong the board measures the wrong thing:
MiniMax lost its reasoning chain between turns (1/10 recall dropped vs 7/10
replayed) and a retired model id that ``/v1/models`` still listed 404'd on
every call.

``crucibleforge conform`` proves the protocol per model, through the bench's
OWN request path (``Provider.chat`` + the runner's message builders), so a
PASS means the bench itself would talk to the model correctly.

The fake here is an HTTP-level one (it replaces ``httpx.Client``), so the
whole real path is exercised: ``providers.Provider.chat`` ->
``api.stream_chat`` -> the wire, including ``reasoning_split``, the
``reasoning_content`` channel and the inline ``<think>`` dialect.  Every
check is driven through it: c1 served, c2 reasoning, c3 chain (the MiniMax
defect), c4 tools, c5 sampler, c6 budget — each PASS, WARN and FAIL path —
plus the record file, the protocol fingerprint and the CLI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crucibleforge import api, cli, conformance, providers
from crucibleforge.providers import clear_cache

ROOT = Path(__file__).resolve().parent.parent

#: the number the fake model thinks of in the c3 probe
CHAIN_NUMBER = "482913"


# ------------------------------------------------------- the fake server

class _FakeResponse:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status_code = status
        self.headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_text(self):
        for line in self._lines:
            yield line

    def read(self):
        return "".join(self._lines).encode()


class _FakeClient:
    """Records every request body and answers from a scripted server.

    The script is a callable: ``script(body) -> (status, sse_lines)``.  It
    sees the REAL request, so a check that sent the wrong bytes (no
    ``reasoning_split``, an assistant turn without its reasoning, a tool call
    that was never answered) gets an answer that does not match what the
    bench would get.
    """

    bodies: list = []
    script = None

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        body = kw.get("json")
        _FakeClient.bodies.append(body)
        status, lines = _FakeClient.script(body)
        return _FakeResponse(lines, status)


def _sse(chunks: list[dict]) -> list[str]:
    return [f"data: {json.dumps(c)}\n" for c in chunks] + ["data: [DONE]\n"]


def _chunk(delta: dict, finish=None, model="m") -> dict:
    c = {"model": model, "choices": [{"delta": delta}]}
    if finish:
        c["choices"][0]["finish_reason"] = finish
    return c


def _usage(reasoning: bool = False) -> dict:
    """The usage block. ``reasoning_tokens`` is only reported when the
    reasoning really was streamed -- a model that does not think reports 0,
    which is what the c2 check looks at."""
    details = {"reasoning_tokens": 3 if reasoning else 0}
    return {"model": "m", "choices": [],
            "usage": {"prompt_tokens": 9, "completion_tokens": 7,
                      "completion_tokens_details": details}}


def _last_text(body: dict) -> str:
    """The content of the last message of a request."""
    messages = body.get("messages") or []
    return str(messages[-1].get("content") or "") if messages else ""


#: the answers the fake server gives, per request kind
def _default_script(body: dict) -> tuple[int, list[str]]:
    messages = body.get("messages") or []
    text = _last_text(body)
    split = body.get("reasoning_split")

    # --- the c1 / c5 "ready" probe
    if "single word: ready" in text:
        return 200, _sse([_chunk({"content": "ready"}, "stop"), _usage()])

    # --- the c2 reasoning probe
    if "A train leaves at 09:15" in text:
        if split:
            # the reasoning arrives in the reasoning_content CHANNEL
            return 200, _sse([_chunk({"reasoning_content": "09:15 + 4h20 = 13:35"}),
                              _chunk({"content": "13:35"}, "stop"), _usage(True)])
        # ... or INLINE in the content, as the same model does without
        # reasoning_split
        return 200, _sse([_chunk({"content": "<think>09:15 + 4h20 = 13:35</think>"}),
                          _chunk({"content": "13:35"}, "stop"), _usage(True)])

    # --- the c3 chain probe, turn 1: think of a number, say only OK
    if "random 6-digit number" in text:
        if split:
            return 200, _sse([_chunk({"reasoning_content":
                                      f"Let me pick {CHAIN_NUMBER} and remember it."}),
                              _chunk({"content": "OK"}, "stop"), _usage(True)])
        return 200, _sse([_chunk({"content":
                                  f"<think>Let me pick {CHAIN_NUMBER} and remember it.</think>"}),
                          _chunk({"content": "OK"}, "stop"), _usage(True)])

    # --- the c3 chain probe, turn 2: the model recalls ONLY if its own
    # reasoning came back to it in the history
    if "What number did you pick" in text:
        seen = ""
        for m in messages:
            if m.get("role") == "assistant":
                seen += str(m.get("reasoning_content") or "")
        if CHAIN_NUMBER in seen:
            return 200, _sse([_chunk({"content": CHAIN_NUMBER}, "stop"), _usage()])
        return 200, _sse([_chunk({"content": "I don't know, I never said."},
                                  "stop"), _usage()])

    # --- the c4 tool round trip
    if body.get("tools"):
        answered = any(m.get("role") == "tool" for m in messages)
        if not answered:
            return 200, _sse([
                _chunk({"tool_calls": [{"index": 0, "id": "call_1",
                                        "function": {"name": "get_weather",
                                                     "arguments": '{"city": "Oslo"}'}}]}),
                _chunk({}, "tool_calls"), _usage()])
        # the tool result is answered: the final answer uses it
        return 200, _sse([_chunk({"content": "It is 7 degrees and raining."},
                                  "stop"), _usage()])

    # --- the c6 coding prompt
    if "merge_intervals" in text:
        return 200, _sse([_chunk({"content": "```python\ndef merge_intervals(x): ...\n```"},
                                  "stop"), _usage()])

    return 200, _sse([_chunk({"content": "ok"}, "stop"), _usage()])


#: a script that 404s every request (a retired model id)
def _not_found_script(body: dict) -> tuple[int, list[str]]:
    return 404, ['{"error": {"message": "model not found"}}']


#: a script that rejects the tool-result turn (a malformed history)
def _bad_history_script(body: dict) -> tuple[int, list[str]]:
    if any(m.get("role") == "tool" for m in body.get("messages") or []):
        return 400, ['{"error": {"message": "invalid tool_call_id"}}']
    return _default_script(body)


#: a script that rejects a sampler parameter (kimi-k3 refuses temperature != 1)
def _bad_sampler_script(body: dict) -> tuple[int, list[str]]:
    if body.get("temperature") not in (None, 1.0):
        return 400, ['{"error": {"message": "temperature must be 1"}}']
    return _default_script(body)


#: a script whose answer leaks an unparsed think dialect into the answer
def _leaky_script(body: dict) -> tuple[int, list[str]]:
    if "A train leaves at 09:15" in _last_text(body):
        return 200, _sse([_chunk({"content": "<|scratchpad|>13:35</|scratchpad|>"},
                                  "stop"), _usage()])
    return _default_script(body)


#: a script that answers the c6 prompt with nothing (a board-budget overrun)
def _empty_script(body: dict) -> tuple[int, list[str]]:
    if "merge_intervals" in _last_text(body):
        return 200, _sse([_chunk({}, "length"), _usage()])
    return _default_script(body)


#: a script that says the sampler is ignored
def _ignores_sampler_script(body: dict) -> tuple[int, list[str]]:
    if "single word: ready" in _last_text(body):
        return 200, _sse([_chunk({"content": "ready (temperature is ignored here)"},
                                  "stop"), _usage()])
    return _default_script(body)


# ------------------------------------------------------------- fixtures

def _cfg(*, reasoning_replay=True, sampler=None, model_id="m"):
    provider = {"type": "openai", "base_url": "http://fake.invalid/v1",
                "api_key": "k", "concurrency": 1}
    if reasoning_replay is not None:
        provider["reasoning_replay"] = reasoning_replay
    entry = {"name": "m", "provider": "fake", "model_id": model_id,
             "context_length": 32768}
    if sampler:
        entry["sampler"] = sampler
    return {
        "providers": {"fake": provider},
        "defaults": {"regime": "deployed",
                     "thinking_max_tokens_factor": 4,
                     "thinking_max_tokens_cap": 24576,
                     "thinking_max_tokens_floor": 16384,
                     "recommended_samplers": []},
        "models": [entry],
        "judge": {"candidates": []},
    }


@pytest.fixture(autouse=True)
def _fake_server(monkeypatch):
    _FakeClient.bodies = []
    _FakeClient.script = _default_script
    monkeypatch.setattr(api.httpx, "Client", _FakeClient)
    clear_cache()
    yield
    clear_cache()


def _run(cfg, script=None):
    """Run the checks for one model against the fake server."""
    if script is not None:
        _FakeClient.script = script
    entry = cfg["models"][0]
    return conformance.run_model(cfg, entry)


def _check(rec, cid):
    return next(c for c in rec.checks if c.id == cid)


# --------------------------------------------------------------- the checks

def test_c1_served_passes_and_records_the_model_the_api_reports():
    rec = _run(_cfg())
    c = _check(rec, "c1")
    assert c.status == conformance.PASS
    assert "served by 'm'" in c.detail
    assert rec.served_model == "m"


def test_c1_served_fails_on_a_4xx():
    """A retired id that /v1/models still lists answers 404 — that is a FAIL,
    not a warning: the bench would score nothing for this model."""
    rec = _run(_cfg(), _not_found_script)
    c = _check(rec, "c1")
    assert c.status == conformance.FAIL
    assert "404" in c.detail


def test_c2_reasoning_passes_and_says_where_the_reasoning_arrived():
    rec = _run(_cfg(reasoning_replay=True))
    c = _check(rec, "c2")
    assert c.status == conformance.PASS
    assert "reasoning_content field" in c.detail
    assert "no think tags" in c.detail


def test_c2_reasoning_reports_inline_think_tags():
    """Without reasoning_split the same model thinks INLINE: the harness must
    still parse it out of the answer."""
    rec = _run(_cfg(reasoning_replay=False))
    c = _check(rec, "c2")
    assert c.status == conformance.PASS
    assert "inline" in c.detail


def test_c2_reasoning_fails_when_think_tags_leak_into_the_answer():
    """A dialect the harness does not parse leaks the tags into the answer —
    the graders then read the CoT as the reply (the gemma4 case of
    2026-10-24, three models scored 0)."""
    rec = _run(_cfg(), _leaky_script)
    c = _check(rec, "c2")
    assert c.status == conformance.FAIL
    assert "leaked" in c.detail


def test_c2_reasoning_is_info_for_a_model_that_does_not_think():
    """A model that answers directly has no reasoning to place: the check
    says so (INFO) instead of pretending it passed."""
    def script(body):
        if "A train leaves" in _last_text(body):
            return 200, _sse([_chunk({"content": "13:35"}, "stop"), _usage()])
        return _default_script(body)

    rec = _run(_cfg(), script)
    c = _check(rec, "c2")
    assert c.status == conformance.INFO
    assert "no reasoning channel" in c.detail


def test_c3_chain_passes_when_the_current_replay_setting_is_the_right_one():
    """The model recalls its number because the chain goes back — which is
    what the current setting does."""
    rec = _run(_cfg(reasoning_replay=True))
    c = _check(rec, "c3")
    assert c.status == conformance.PASS
    assert "current replay setting holds" in c.detail


def test_c3_chain_fails_when_the_opposite_setting_recalls_better():
    """THE MINIMAX DEFECT: the harness drops the reasoning between turns, so
    the model cannot remember what it thought.  The check must say so."""
    rec = _run(_cfg(reasoning_replay=False))
    c = _check(rec, "c3")
    assert c.status == conformance.FAIL
    assert "replay setting is wrong for this model" in c.detail


def test_c3_chain_is_info_for_a_model_that_does_not_think():
    """No reasoning channel: there is no chain to replay, so the check says
    so instead of guessing."""
    def script(body):
        text = _last_text(body)
        if "random 6-digit number" in text:
            return 200, _sse([_chunk({"content": "OK"}, "stop"), _usage()])
        if "What number did you pick" in text:
            return 200, _sse([_chunk({"content": "I don't know."}, "stop"), _usage()])
        return _default_script(body)

    rec = _run(_cfg(reasoning_replay=True), script)
    c = _check(rec, "c3")
    assert c.status == conformance.INFO
    assert "not a thinking model" in c.detail


def test_c3_chain_is_info_when_the_model_writes_the_number_down():
    """The fact must live ONLY in the reasoning.  A model that writes the
    number down cannot separate the two settings, so the check says so
    instead of guessing."""
    def script(body):
        text = _last_text(body)
        if "random 6-digit number" in text:
            return 200, _sse([_chunk({"reasoning_content":
                                      f"Let me pick {CHAIN_NUMBER}."}),
                              _chunk({"content": f"I picked {CHAIN_NUMBER}."},
                                     "stop"), _usage(True)])
        return _default_script(body)

    rec = _run(_cfg(reasoning_replay=True), script)
    c = _check(rec, "c3")
    assert c.status == conformance.INFO
    assert "wrote the number down" in c.detail


def test_c4_tools_passes_on_a_well_formed_round_trip():
    rec = _run(_cfg())
    c = _check(rec, "c4")
    assert c.status == conformance.PASS
    # the tool result really went back into the conversation
    tool_turns = [b for b in _FakeClient.bodies
                  if any(m.get("role") == "tool" for m in b["messages"])]
    assert tool_turns, _FakeClient.bodies
    assert any(m["role"] == "tool" and m["tool_call_id"] == "call_1"
               for m in tool_turns[0]["messages"]), tool_turns[0]["messages"]
    # ... and the assistant turn carried the call the result answers
    assistant = [m for m in tool_turns[0]["messages"] if m["role"] == "assistant"]
    assert assistant and assistant[0].get("tool_calls"), tool_turns[0]["messages"]


def test_c4_tools_fails_when_the_api_rejects_the_tool_result_turn():
    """A malformed history (an unanswered tool call, a wrong tool_call_id) is
    a 4xx — the bench's tool rows would all error."""
    rec = _run(_cfg(), _bad_history_script)
    c = _check(rec, "c4")
    assert c.status == conformance.FAIL
    assert "400" in c.detail


def test_c4_tools_warns_when_the_model_never_calls_the_tool():
    def script(body):
        if "get_weather" in _last_text(body):
            return 200, _sse([_chunk({"content": "It is probably cold."},
                                      "stop"), _usage()])
        return _default_script(body)

    rec = _run(_cfg(), script)
    c = _check(rec, "c4")
    assert c.status == conformance.WARN
    assert "did not call the tool" in c.detail


def test_c5_sampler_passes_when_the_api_accepts_it():
    rec = _run(_cfg(sampler={"temperature": 1.0, "top_p": 0.95}))
    c = _check(rec, "c5")
    assert c.status == conformance.PASS
    assert "temperature=1.0" in c.detail
    assert "top_p=0.95" in c.detail
    # the sampler really went on the wire
    probe = [b for b in _FakeClient.bodies
             if "single word: ready" in str(b["messages"][-1]["content"])]
    assert probe and probe[-1]["temperature"] == 1.0


def test_c5_sampler_fails_when_the_api_rejects_a_parameter():
    """kimi-k3 rejects any temperature but 1: the profile's sampler must be
    one the API accepts, or every row errors."""
    rec = _run(_cfg(sampler={"temperature": 0.0}), _bad_sampler_script)
    c = _check(rec, "c5")
    assert c.status == conformance.FAIL
    assert "rejected" in c.detail


def test_c5_sampler_notes_a_parameter_the_api_says_it_ignores():
    rec = _run(_cfg(sampler={"temperature": 1.0}), _ignores_sampler_script)
    c = _check(rec, "c5")
    assert c.status == conformance.WARN
    assert "ignores temperature" in c.detail


def test_c6_budget_passes_when_the_answer_fits():
    rec = _run(_cfg())
    c = _check(rec, "c6")
    assert c.status == conformance.PASS
    assert "board budget" in c.detail


def test_c6_budget_warns_on_an_empty_answer_at_the_board_budget():
    """The MiniMax symptom: truncated rows were 100% empty at the board
    budget.  The answer needs the escalation rung (see Room)."""
    rec = _run(_cfg(), _empty_script)
    c = _check(rec, "c6")
    assert c.status == conformance.WARN
    assert "escalation rung" in c.detail
    assert "Room" in c.detail


def test_c6_budget_fails_on_an_empty_answer_that_did_not_run_out_of_budget():
    def script(body):
        if "merge_intervals" in _last_text(body):
            return 200, _sse([_chunk({}, "stop"), _usage()])
        return _default_script(body)

    rec = _run(_cfg(), script)
    c = _check(rec, "c6")
    assert c.status == conformance.FAIL
    assert "empty answer" in c.detail


# --------------------------------------------------------- the whole model

def test_the_record_is_written_with_the_protocol_fingerprint(tmp_path,
                                                            monkeypatch):
    from crucibleforge import config as cfgmod
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    cfg = _cfg()
    rec = conformance.conform(cfg, cfg["models"])[0]
    assert rec.verdict in (conformance.PASS, conformance.WARN)
    path = conformance.record_path("m")
    assert path.exists()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["verdict"] == rec.verdict
    assert stored["suite_revision"]
    assert stored["protocol"]["provider_type"] == "openai"
    assert stored["protocol"]["reasoning_replay"] is True
    assert "reasoning_split" in stored["protocol"]["extra_body_keys"]
    assert stored["protocol"]["sampler"]["source"]
    assert {c["id"] for c in stored["checks"]} == set(conformance.CHECK_IDS)
    for c in stored["checks"]:
        assert c["status"] in (conformance.PASS, conformance.WARN,
                               conformance.FAIL, conformance.INFO)
        assert c["detail"]


def test_the_fingerprint_moves_with_the_protocol():
    """A harness change (the replay on/off, another extra_body key, another
    sampler) must move the fingerprint — that is how a stale row is told
    from an old one."""
    cfg = _cfg()
    entry = cfg["models"][0]
    prov = providers.get_provider(cfg, "fake")
    on = conformance.protocol_fingerprint(cfg, entry, prov)
    prov.reasoning_replay = False
    off = conformance.protocol_fingerprint(cfg, entry, prov)
    assert on != off
    assert "reasoning_split" in on["extra_body_keys"]
    assert "reasoning_split" not in off["extra_body_keys"]


def test_a_rig_model_that_is_not_loaded_is_skipped(tmp_path, monkeypatch):
    """conform never loads a model behind the bench's back: a rig model that
    is not resident is reported as SKIP."""
    from crucibleforge import config as cfgmod
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    cfg = _cfg()
    cfg["providers"]["rig"] = {"type": "studioforge",
                               "base_url": "http://rig.invalid/v1",
                               "api_key": "k"}
    entry = {"name": "rig-model", "provider": "rig", "model_id": "rig-model",
             "context_length": 32768}
    monkeypatch.setattr(conformance, "_rig_resident", lambda p, m: False)
    rec = conformance.run_model(cfg, entry)
    assert rec.verdict == conformance.SKIP
    assert rec.note and "not loaded" in rec.note
    assert rec.checks == []


# --------------------------------------------------------------- the CLI

def _args(**kw):
    base = {"models": "all", "json": False, "allow_load": False, "profile": None}
    base.update(kw)
    return type("A", (), base)()


def test_the_cli_runs_the_checks_and_writes_the_records(tmp_path, monkeypatch,
                                                         capsys):
    from crucibleforge import config as cfgmod
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    cfg = _cfg()
    monkeypatch.setattr(conformance, "with_profile", lambda c, p=None: c)
    rc = cli.cmd_conform(_args(), cfg)
    out = capsys.readouterr().out
    assert rc == 0
    assert "c1" in out and "c6" in out
    assert (tmp_path / "conformance" / "m.json").exists()


def test_the_cli_json_output_is_machine_readable(tmp_path, monkeypatch, capsys):
    from crucibleforge import config as cfgmod
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    cfg = _cfg()
    monkeypatch.setattr(conformance, "with_profile", lambda c, p=None: c)
    rc = cli.cmd_conform(_args(json=True), cfg)
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload[0]["model_label"] == "m"
    assert payload[0]["checks"][0]["id"] == "c1"


def test_the_cli_reports_a_fail(tmp_path, monkeypatch, capsys):
    """A FAIL is the gate's signal: the exit code says so."""
    from crucibleforge import config as cfgmod
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    cfg = _cfg()
    monkeypatch.setattr(conformance, "with_profile", lambda c, p=None: c)
    _FakeClient.script = _not_found_script
    rc = cli.cmd_conform(_args(), cfg)
    assert rc == 1
    assert "FAIL" in capsys.readouterr().out
