"""Part 3 of the conformance plan: the protocol stamp (2026-11-03).

R3 said: rows do not record the request protocol that produced them, so a
harness fix cannot tell which rows are stale.  This file proves the stamp:
every row carries the protocol it was measured under, and the stamp is the
same fingerprint the conformance record carries.

The fake here is the HTTP-level one (it replaces httpx.Client), so the whole
real path runs: providers.Provider.chat -> api.stream_chat -> the wire.  No
network, no rig.
"""
from __future__ import annotations

import json

import pytest

from crucibleforge import api, config, conformance, providers, runner
from crucibleforge.providers import clear_cache


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
    """Records every request body and answers from a scripted server."""

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

    def get(self, url, **kw):
        # /models is not supported by the fake: the provider falls back to
        # its optimistic path (an openai provider is assumed to serve it)
        return _FakeResponse(['{"data": []}'], 404)


def _sse(chunks: list[dict]) -> list[str]:
    return [f"data: {json.dumps(c)}\n" for c in chunks] + ["data: [DONE]\n"]


def _chunk(delta: dict, finish=None, model="m") -> dict:
    c = {"model": model, "choices": [{"delta": delta}]}
    if finish:
        c["choices"][0]["finish_reason"] = finish
    return c


def _usage(reasoning: bool = False) -> dict:
    details = {"reasoning_tokens": 3 if reasoning else 0}
    return {"model": "m", "choices": [],
            "usage": {"prompt_tokens": 9, "completion_tokens": 7,
                      "completion_tokens_details": details}}


def _last_text(body: dict) -> str:
    messages = body.get("messages") or []
    return str(messages[-1].get("content") or "") if messages else ""


def _default_script(body: dict) -> tuple[int, list[str]]:
    """A model that behaves: it serves, it thinks, it remembers its chain,
    it calls the tool, it answers inside the budget."""
    messages = body.get("messages") or []
    text = _last_text(body)
    split = body.get("reasoning_split")

    if "single word: ready" in text:
        return 200, _sse([_chunk({"content": "ready"}, "stop"), _usage()])

    if "A train leaves at 09:15" in text:
        if split:
            return 200, _sse([_chunk({"reasoning_content": "09:15 + 4h20 = 13:35"}),
                              _chunk({"content": "13:35"}, "stop"), _usage(True)])
        return 200, _sse([_chunk({"content": "<think>09:15 + 4h20 = 13:35</think>"}),
                          _chunk({"content": "13:35"}, "stop"), _usage(True)])

    if "random 6-digit number" in text:
        return 200, _sse([_chunk({"reasoning_content": "let me pick 482913"}),
                          _chunk({"content": "OK"}, "stop"), _usage(True)])

    if "What number did you pick" in text:
        seen = ""
        for m in messages:
            if m.get("role") == "assistant":
                seen += str(m.get("reasoning_content") or "")
        if "482913" in seen:
            return 200, _sse([_chunk({"content": "482913"}, "stop"), _usage()])
        return 200, _sse([_chunk({"content": "I don't know, I never said."},
                                  "stop"), _usage()])

    if body.get("tools"):
        answered = any(m.get("role") == "tool" for m in messages)
        if not answered:
            return 200, _sse([
                _chunk({"tool_calls": [{"index": 0, "id": "call_1",
                                        "function": {"name": "get_weather",
                                                     "arguments": '{"city": "Oslo"}'}}]}),
                _chunk({}, "tool_calls"), _usage()])
        return 200, _sse([_chunk({"content": "It is 7 degrees and raining."},
                                  "stop"), _usage()])

    if "merge_intervals" in text:
        return 200, _sse([_chunk({"content": "code"}, "stop"), _usage()])

    return 200, _sse([_chunk({"content": "ok"}, "stop"), _usage()])


# ------------------------------------------------------------- fixtures

def _cfg(**kw):
    provider = {"type": "openai", "base_url": "http://fake.invalid/v1",
                "api_key": "k", "concurrency": 1,
                # the replay is ON: it is the MiniMax protocol, and with it
                # off the chain is dropped (the defect c3 exists for)
                "reasoning_replay": True}
    provider.update(kw.pop("provider", {}))
    entry = {"name": "m", "provider": "fake", "model_id": "m",
             "context_length": 32768}
    entry.update(kw.pop("entry", {}))
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


CASE = {"id": "I1", "category": "instruct", "prompt": "x", "max_tokens": 8,
        "grader": "contains", "grader_config": {"needles": ["ok"]}}


@pytest.fixture(autouse=True)
def _fake_server(monkeypatch, tmp_path):
    _FakeClient.bodies = []
    _FakeClient.script = _default_script
    monkeypatch.setattr(api.httpx, "Client", _FakeClient)
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    clear_cache()
    yield
    clear_cache()


def _run(cfg=None):
    """One model, one case, through the real runner."""
    cfg = cfg or _cfg()
    return runner.run_models(cfg, cfg["models"], [CASE])


def _rows(label="m"):
    path = config.results_dir() / f"transcripts_{label}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _record(label="m"):
    path = config.results_dir() / "conformance" / f"{label}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# --------------------------------------------------------- the protocol stamp

def test_a_row_records_the_protocol_it_was_measured_under():
    """R3: a row says which request protocol produced it."""
    summary = _run()
    assert summary["m"]["rows"] == 1
    row = _rows()[0]
    assert row["protocol"]["provider_type"] == "openai"
    assert row["protocol"]["reasoning_replay"] is True
    assert "reasoning_split" in row["protocol"]["extra_body_keys"]
    assert "extra_body_keys" in row["protocol"]
    assert "sampler" in row["protocol"]


def test_the_row_protocol_is_the_one_the_conformance_record_carries():
    """The stamp and the record must be the same fingerprint, or the gate
    would call every row stale."""
    cfg = _cfg()
    conformance.conform(cfg, cfg["models"])
    _run(cfg)
    row = _rows()[0]
    assert row["protocol"] == _record()["protocol"]


def test_the_stamp_moves_with_the_protocol():
    """A harness change moves the stamp: the request grows a field."""
    _run()
    plain = _rows()[0]["protocol"]
    assert "reasoning_split" in plain["extra_body_keys"]
    cfg = _cfg(entry={"extra_body": {"foo": 1}})
    _run(cfg)
    grown = _rows()[-1]["protocol"]
    assert "foo" in grown["extra_body_keys"]
    assert "foo" not in plain["extra_body_keys"]
    assert grown != plain
