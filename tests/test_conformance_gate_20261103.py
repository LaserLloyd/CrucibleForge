"""Part 3 of the conformance plan: the gate, the board notes and the
protocol stamp (2026-11-03).

R2/R3 said: nothing proves per model that the harness's conversation is
well-formed, and rows do not record the request protocol that produced them,
so a harness fix cannot tell which rows are stale.  Part 3 closes both:

* every row carries the protocol it was measured under (the conformance
  fingerprint);
* a model is benched only under a protocol that has been PROVEN for it --
  the gate runs the checks when the record is missing or stale, and a FAIL
  means the model is not run at all;
* \'\'--skip-conformance\'\' overrides the gate (and is logged).

The fake here is the HTTP-level one (it replaces httpx.Client), so the whole
real path runs: providers.Provider.chat -> api.stream_chat -> the wire.  No
network, no rig.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from datetime import datetime, timedelta, timezone

from crucibleforge import api, cli, conformance, config, providers, runner
from crucibleforge.providers import clear_cache

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
        if split:
            return 200, _sse([_chunk({"reasoning_content":
                                      f"Let me pick {CHAIN_NUMBER} and remember it."}),
                              _chunk({"content": "OK"}, "stop"), _usage(True)])
        return 200, _sse([_chunk({"content":
                                  f"<think>Let me pick {CHAIN_NUMBER} and remember it.</think>"}),
                          _chunk({"content": "OK"}, "stop"), _usage(True)])

    if "What number did you pick" in text:
        seen = ""
        for m in messages:
            if m.get("role") == "assistant":
                seen += str(m.get("reasoning_content") or "")
        if CHAIN_NUMBER in seen:
            return 200, _sse([_chunk({"content": CHAIN_NUMBER}, "stop"), _usage()])
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
        return 200, _sse([_chunk({"content": "```python\ndef merge_intervals(x): ...\n```"},
                                  "stop"), _usage()])

    return 200, _sse([_chunk({"content": "ok"}, "stop"), _usage()])


def _not_found_script(body: dict) -> tuple[int, list[str]]:
    """A retired model id: every call 404s."""
    return 404, ['{"error": {"message": "model not found"}}']


def _empty_budget_script(body: dict) -> tuple[int, list[str]]:
    """The board budget is not enough for the coding prompt: an empty answer
    with finish=length (the WARN path of c6)."""
    if "merge_intervals" in _last_text(body):
        return 200, _sse([_chunk({}, "length"), _usage()])
    return _default_script(body)


# ------------------------------------------------------------- fixtures

def _cfg(**kw):
    provider = {"type": "openai", "base_url": "http://fake.invalid/v1",
                "api_key": "k", "concurrency": 1,
                # the replay is ON: it is the MiniMax protocol, and with it
                # off the chain is dropped (the defect c3 exists for)
                "reasoning_replay": True,
                # a hosted provider: its docs are read and dated (c7)
                "docs": "https://fake.invalid/docs",
                "docs_verified": datetime.now(timezone.utc).date().isoformat()}
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


def _run(cfg=None, *, script=None, **kw):
    """One model, one case, through the real runner."""
    if script is not None:
        _FakeClient.script = script
    cfg = cfg or _cfg()
    return runner.run_models(cfg, cfg["models"], [CASE], **kw)


def _gate(cfg=None, *, skip=False):
    """The CLI's conformance gate for the selected models."""
    cfg = cfg or _cfg()
    return cli._conformance_gate(cfg, cfg["models"], skip=skip)


def _record(label="m"):
    path = config.results_dir() / "conformance" / f"{label}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _fake_rig(monkeypatch):
    """The rig's own calls (load, status) are faked; the chat still goes
    through the HTTP fake, so the checks see the real request path."""
    P = providers.Provider
    monkeypatch.setattr(P, "alive", lambda self: True)
    monkeypatch.setattr(P, "is_available", lambda self, mid: True)
    monkeypatch.setattr(P, "switch_model", lambda self, mid, ctx=None: 1.0)
    monkeypatch.setattr(P, "live_context", lambda self, mid: None)
    monkeypatch.setattr(P, "loaded_plan_for", lambda self, mid: {})
    monkeypatch.setattr(P, "workers", lambda self, mid: 1)
    monkeypatch.setattr(P, "is_loaded", lambda self, mid: True)
    monkeypatch.setattr(P, "model_capabilities", lambda self, mid: set())


def _rows(cfg=None, label="m"):
    path = (config.results_dir() / f"transcripts_{label}.jsonl")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _meta(label="m"):
    path = config.results_dir() / f"meta_{label}.json"
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
    _gate(cfg)
    _run(cfg)
    row = _rows()[0]
    rec = _record()
    assert row["protocol"] == rec["protocol"]


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


# ---------------------------------------------------------------- the gate

def test_the_gate_runs_the_checks_when_the_record_is_missing():
    """No record -> the checks run, the record is written, the model runs."""
    cfg = _cfg()
    ok, bad = _gate(cfg)
    assert ok and not bad
    rec = _record()
    assert rec["verdict"] == conformance.PASS
    assert [c["id"] for c in rec["checks"]] == list(conformance.CHECK_IDS)


def test_the_gate_does_not_re_check_a_current_record():
    """A record for the current protocol is used as it is."""
    cfg = _cfg()
    _gate(cfg)
    n = len(_FakeClient.bodies)
    _gate(cfg)
    probes = [b for b in _FakeClient.bodies[n:]
              if "single word: ready" in _last_text(b or {})]
    assert probes == []


def test_the_gate_re_checks_when_the_protocol_changed():
    """A harness change makes the old record stale: the checks run again."""
    cfg = _cfg()
    _gate(cfg)
    stale = _record()
    # the request grows a field: the fingerprint moves
    cfg2 = _cfg(entry={"extra_body": {"foo": 1}})
    _gate(cfg2)
    fresh = _record()
    assert fresh["protocol"] != stale["protocol"]
    assert fresh["ts"] >= stale["ts"]


def test_a_conformance_fail_means_the_model_is_not_run():
    """A FAIL blocks the model: it is not run, and the board says why."""
    _FakeClient.script = _not_found_script
    cfg = _cfg()
    ok, bad = _gate(cfg)
    assert ok == []
    assert "conformance FAIL: c1" in bad["m"]
    meta = _meta()
    assert meta["failed"] is True
    assert "conformance FAIL: c1" in meta["error"]


def test_a_conformance_warn_still_runs_the_model():
    """A WARN is a warning, not a gate: the model runs and the record says
    what to look at."""
    _FakeClient.script = _empty_budget_script
    cfg = _cfg()
    ok, bad = _gate(cfg)
    assert ok and not bad
    rec = _record()
    assert rec["verdict"] == conformance.WARN
    assert any(c["id"] == "c6" and c["status"] == conformance.WARN
               for c in rec["checks"])


def test_skip_conformance_lets_the_model_run_and_is_logged(caplog):
    """The override: the model runs without a proven protocol, and the log
    says so."""
    _FakeClient.script = _not_found_script
    with caplog.at_level("WARNING"):
        ok, bad = _gate(_cfg(), skip=True)
    assert ok and not bad
    assert any("--skip-conformance" in r.getMessage() for r in caplog.records)


def test_a_current_record_is_not_re_checked_after_a_fail():
    """A FAIL is remembered: the model is not benched again and again."""
    _FakeClient.script = _not_found_script
    cfg = _cfg()
    _gate(cfg)
    n = len(_FakeClient.bodies)
    ok, bad = _gate(cfg)
    assert ok == []
    assert bad["m"]
    # no probe from the checks was sent: the record already said FAIL
    probes = [b for b in _FakeClient.bodies[n:]
              if "single word: ready" in _last_text(b or {})]
    assert probes == []


def test_a_rig_model_is_gated_right_after_the_load(monkeypatch):
    """A rig model can only be checked while it is loaded: the runner gates
    it there, and a FAIL means it is not run."""
    _fake_rig(monkeypatch)
    _FakeClient.script = _not_found_script
    cfg = _cfg(provider={"type": "studioforge"})
    summary = _run(cfg)
    assert summary["m"]["failed"] is True
    assert "conformance FAIL" in summary["m"]["error"]
    assert _rows() == []


def test_a_rig_model_with_a_proven_protocol_runs(monkeypatch):
    """The rig gate lets a model through when the checks pass."""
    _fake_rig(monkeypatch)
    cfg = _cfg(provider={"type": "studioforge"})
    summary = _run(cfg)
    assert summary["m"]["rows"] == 1
    assert _record()["verdict"] in (conformance.PASS, conformance.WARN)


# ------------------------------------------------------- the vendor docs (c7)

def _c7(cfg):
    prov = providers.get_provider(cfg, "fake")
    return conformance._c7_docs(cfg, prov)


def test_a_hosted_provider_without_docs_warns():
    """Nobody read the protocol the bench assumes: the row says so."""
    cfg = _cfg()
    cfg["providers"]["fake"].pop("docs")
    c = _c7(cfg)
    assert c.id == "c7"
    assert c.status == conformance.WARN
    assert "docs" in c.detail


def test_a_hosted_provider_without_a_verification_date_warns():
    cfg = _cfg()
    cfg["providers"]["fake"].pop("docs_verified")
    c = _c7(cfg)
    assert c.status == conformance.WARN


def test_a_hosted_provider_with_stale_docs_warns():
    """The review is older than 120 days: read them again."""
    cfg = _cfg()
    old = (datetime.now(timezone.utc).date()
           - timedelta(days=conformance.DOCS_MAX_AGE_DAYS + 1)).isoformat()
    cfg["providers"]["fake"]["docs_verified"] = old
    c = _c7(cfg)
    assert c.status == conformance.WARN
    assert str(conformance.DOCS_MAX_AGE_DAYS) in c.detail


def test_a_hosted_provider_with_fresh_docs_passes():
    cfg = _cfg()
    c = _c7(cfg)
    assert c.status == conformance.PASS


def test_a_local_provider_is_not_asked_for_vendor_docs():
    """A local server has no vendor documentation."""
    cfg = _cfg(provider={"base_url": "http://127.0.0.1:1234/v1"})
    cfg["providers"]["fake"].pop("docs")
    cfg["providers"]["fake"].pop("docs_verified")
    c = _c7(cfg)
    assert c.status == conformance.INFO


def test_the_docs_check_is_part_of_the_record():
    """c7 runs with the other checks, so the record always carries it."""
    cfg = _cfg()
    conformance.conform(cfg, cfg["models"])
    rec = _record()
    assert "c7" in {c["id"] for c in rec["checks"]}
