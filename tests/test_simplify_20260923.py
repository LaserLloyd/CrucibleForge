"""2026-09-23 simplification round: one benchmark scored as Chat + Coding,
board hygiene, report formatting, run-time fixes and the grader bugs the test
audit found (correct answers marked wrong)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from crucibleforge import api, cli, config, graders, judge, preflight, report, runner, studioforge
from crucibleforge.api import ChatResult, GenerationRejected, TransportError


def _case(cid):
    return next(c for c in config.load_cases() if c["id"] == cid)


# ------------------------------------------------------------ report shape

def _bench_row(cid, cat, grade="pass", run="r1", ts="2026-09-23T01:00:00+00:00", **kw):
    from crucibleforge.version import revision
    return {"bench_run_id": run, "bench_revision": revision(), "profile": "bench",
            "case_id": cid, "repeat": 1, "turn": None, "category": cat, "grade": grade,
            "ts": ts, "metrics": {"tok_per_s": 30.0}, **kw}


def _seed(tmp_path, monkeypatch, label, rows, meta=None):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / f"transcripts_{label}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / f"meta_{label}.json").write_text(json.dumps(
        meta or {"model_id": "x/y", "provider": "p", "profile": "bench",
                 "finished": "2026-09-23T01:30:00+00:00"}))


def test_scorecard_has_only_chat_and_coding_headlines(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "m", [_bench_row("IZ01-logistics-47-words", "instruct")])
    md = report.generate()
    sc = md.split("## Scorecard")[1].split("\n\n")[1]
    header = sc.splitlines()[0]
    assert header == "| # | Model | Chat | Coding | Overall | tok/s | Run date | Notes |"
    assert "T/S" not in md and "| Code |" not in md and "Summary" not in md
    comp_header = md.split("## Components")[1].split("\n\n")[1].splitlines()[0]
    assert "| Programs |" in comp_header and "| Code |" not in comp_header
    # failures + details are NOT in report.md
    assert "## Notes" not in md and (tmp_path / "failures.md").exists()


def test_board_uses_latest_run_and_only_current_bench_rows(tmp_path, monkeypatch):
    rows = [
        _bench_row("IZ01-logistics-47-words", "instruct", "fail", run="old", ts="2026-09-20T00:00:00"),
        _bench_row("IZ01-logistics-47-words", "instruct", "pass", run="new", ts="2026-09-23T00:00:00"),
        {**_bench_row("IZ03-no-letter-e-three-lines", "instruct", "fail"), "profile": "standard"},
        {**_bench_row("IZ04-lowercase-except-http", "instruct", "fail"), "bench_revision": "2.0.0+old"},
    ]
    _seed(tmp_path, monkeypatch, "m", rows)
    kept = report.board_filter("m", None)
    assert [(r["case_id"], r["bench_run_id"]) for r in kept] == [("IZ01-logistics-47-words", "new")]
    labels, stats, dropped = report.board_stats(None, None)
    assert labels == ["m"] and stats["m"]["instruct"]["n"] == 1   # no inflated denominator
    # a model with only older rows is left off, counted in one line
    (tmp_path / "transcripts_ancient.jsonl").write_text(json.dumps(rows[3]) + "\n")
    labels, _, dropped = report.board_stats(None, None)
    assert labels == ["m"] and dropped == 1


def test_markdown_cells_escaped_and_cut_at_word_boundaries():
    assert report._cell("a|b\nc") == "a\\|b c"
    assert report._cell(None) == "-" and report._cell("") == "-"
    cut = report._cut("the quick brown fox jumps over the lazy dog", 20)
    assert cut.endswith("…") and len(cut) <= 20 and not cut[:-1].endswith(("quic", "brow"))
    s = {"meta": {"failed": True, "error": "HTTP 400 | bad\nthing"}, "coverage": None}
    s["coverage"] = report._coverage([], s["meta"], None)
    md_row = report._row(["1", "m", report.notes_cell(s)])
    assert md_row.count("|") - md_row.count("\\|") == 4  # 3 cells, pipes escaped


def test_sections_without_data_are_skipped(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "m", [], meta={"model_id": "x", "profile": "bench",
                                                 "failed": True, "error": "not served"})
    from crucibleforge.version import revision
    meta = json.loads((tmp_path / "meta_m.json").read_text())
    meta["bench_revision"] = revision()
    (tmp_path / "meta_m.json").write_text(json.dumps(meta))
    md = report.generate()
    assert "## Components" not in md          # nobody has a component value
    assert "FAILED: not served" in md
    fails = (tmp_path / "failures.md").read_text()
    assert "## Run details" not in fails      # no rows -> no dash-only table
    assert "run FAILED" in fails


def test_reasoning_tokens_dash_when_not_recorded(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "m", [_bench_row("IZ01-logistics-47-words", "instruct")])
    s = report.model_stats("m")
    assert s["speed"]["reasoning_tokens_total"] is None
    fails = report.render_failures(["m"], {"m": s}, None)
    detail = [l for l in fails.splitlines() if l.startswith("| m |")][0]
    assert detail.split("|")[5].strip() == "-"


def test_failures_capped_per_model(tmp_path, monkeypatch):
    rows = [_bench_row(f"IZ0{i}-x", "instruct", "fail", grade_detail=f"reason {i}")
            for i in range(1, 9)]
    _seed(tmp_path, monkeypatch, "m", rows)
    fails = report.render_failures(["m"], {"m": report.model_stats("m")}, None)
    bullets = [l for l in fails.splitlines() if l.startswith("- [Instruct]")]
    assert len(bullets) == report.FAILURES_PER_MODEL
    assert "… and 3 more" in fails


def test_generic_judged_category_joins_chat_by_weight(tmp_path, monkeypatch):
    rows = [{"bench_run_id": "r", "case_id": "CT1", "repeat": 1, "turn": None,
             "category": "continuity", "rubric": "rp_single", "needs_judge": True,
             "response": "x", "metrics": {},
             "judge": {"judge_failed": False, "refused": False,
                       "scores": {"prose": 8, "character": 8, "dialogue": 8,
                                  "atmosphere": 8, "emotion": 8, "agency": 8}}}]
    _seed(tmp_path, monkeypatch, "m", rows)
    s = report.model_stats("m")
    assert s["judged_generic"]["continuity"]["score"] == pytest.approx(0.8)
    sc = report.scoring_config({"scoring": {"chat": {"rp": 20, "continuity": 10}}})
    card = report.scorecard(s, sc)
    assert card["chat"] == pytest.approx(80.0)           # only continuity measured
    md = report.render_markdown(["m"], {"m": s}, {"scoring": {"chat": {"rp": 20, "continuity": 10}}})
    assert "| Continuity |" in md


# ------------------------------------------------------------ HTML board

def test_html_board_renders_rows_with_no_placeholders(tmp_path, monkeypatch):
    rows = [_bench_row("IZ01-logistics-47-words", "instruct"),
            _bench_row("CZ05-optimal-bst-cost", "coding", "fail")]
    _seed(tmp_path, monkeypatch, "m", rows,
          meta={"model_id": "evil</script><b>x", "provider": "p", "profile": "bench"})
    report.generate()
    html = (tmp_path / "report.html").read_text()
    assert "PLACEHOLDER" not in html and "/*ROWS*/" not in html
    script = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    assert "</script" not in script                     # "</" escaped inside the JSON
    payload = script.split("const rows = ", 1)[1].split(";\n", 1)[0]
    data = json.loads(payload)                          # "<\/" is valid JSON for "</"
    assert data[0]["model_id"] == "evil</script><b>x"
    assert data[0]["label"] == "m" and isinstance(data[0]["coding"], float)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_html_board_script_executes_and_sorts_numerically(tmp_path, monkeypatch):
    from crucibleforge.templates.board import render_html
    rows = [{"rank": i + 1, "label": f"m{i}", "model_id": "x", "provider": "p",
             "chat": None, "coding": c, "overall": c, "tok_s": t, "date": "2026-09-23",
             "notes": "", "tier": 0, "components": {"Programs": c}}
            for i, (c, t) in enumerate([(50.0, 9.0), (80.0, 100.0), (20.0, 30.0)])]
    html = render_html(rows, "sub")
    script = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    harness = r"""
const els = {};
function el(id){ return els[id] || (els[id] = {id, innerHTML:'', textContent:'', handlers:{},
  addEventListener(ev, fn){ this.handlers[ev] = fn; }}); }
const ths = ['rank','label','chat','coding','overall','tok_s','date','notes'].map(k => ({dataset:{k},
  arrow:{textContent:''}, handlers:{}, querySelector(){ return this.arrow; },
  addEventListener(ev, fn){ this.handlers[ev] = fn; }}));
global.document = { readyState: 'complete', getElementById: el,
  querySelectorAll: (sel) => sel.startsWith('th') ? ths : [], addEventListener(){} };
""" + script + r"""
const order = () => [...el('tbody').innerHTML.matchAll(/data-label="(m\d)"/g)].map(m => m[1]).join(',');
const out = {initial: order()};
ths[5].handlers.click();  out.tok_desc = order();   // tok/s: numeric, descending
ths[3].handlers.click();  out.coding_desc = order();
console.log(JSON.stringify(out));
"""
    p = tmp_path / "board.js"
    p.write_text(harness)
    res = subprocess.run(["node", str(p)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["initial"] == "m0,m1,m2"
    assert out["tok_desc"] == "m1,m2,m0"      # 100 > 30 > 9 (a string sort gives 9 > 30)
    assert out["coding_desc"] == "m1,m0,m2"


# ------------------------------------------------------------ vendor stamp

def test_vendor_stamp_only_applies_to_the_model_it_probed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)

    class P:
        name = "p"
        type = "openai"
    stamp = {"model_id": "deepseek-v4-flash", "model_version_resolved": "deepseek-v4-flash-0731",
             "test_date_utc": "2026-09-09", "vendor_probe_source": "GET /v1/models"}
    (tmp_path / "_stamp.json").write_text(json.dumps(stamp))
    runner._write_meta("a", {"model_id": "deepseek-v4-flash"}, P())
    runner._write_meta("b", {"model_id": "some/other-gguf"}, P())
    a = json.loads((tmp_path / "meta_a.json").read_text())
    b = json.loads((tmp_path / "meta_b.json").read_text())
    assert a["model_version_resolved"] == "deepseek-v4-flash-0731"
    assert "model_version_resolved" not in b and "test_date_utc" not in b
    # the legacy stamp (no model_id) — the 2026-09-09 DeepSeek probe — applies to nobody
    (tmp_path / "_stamp.json").write_text(json.dumps({k: v for k, v in stamp.items()
                                                      if k != "model_id"}))
    runner._write_meta("c", {"model_id": "deepseek-v4-flash"}, P())
    assert "model_version_resolved" not in json.loads((tmp_path / "meta_c.json").read_text())


# ------------------------------------------------------------ graders

def test_unterminated_python_fence_is_run_when_the_reply_finished():
    reply = "Here is my solution:\n```python\ndef f(x):\n    return x + 1\n"
    assert graders.extract_python(reply, "stop") == "def f(x):\n    return x + 1"
    # a truncated reply keeps the old behaviour (its code was never finished)
    assert graders.extract_python(reply, "length").startswith("Here is")
    v = graders.grade_python_exec(reply, {"tests": "assert f(1) == 2"}, "stop")
    assert v["grade"] == "pass"


def test_truncated_numeric_answer_needs_an_explicit_answer_line():
    case = {"grader": "numeric", "grader_config": {"answer": 42}}
    cut = ChatResult(response_text="so 6*7 is 42 and then 3", finish_reason="length")
    assert graders.grade(cut, case)["grade"] == "fail"
    cut_ok = ChatResult(response_text="work...\nAnswer: 42", finish_reason="length")
    assert graders.grade(cut_ok, case)["grade"] == "pass"
    done = ChatResult(response_text="it is 42", finish_reason="stop")
    assert graders.grade(done, case)["grade"] == "pass"


def test_tz05_and_tz08_accept_correct_answers():
    tz05 = _case("TZ05-policy-blocks-transfer")["tool_script"][0]
    reply = "I can't move the money yet. Please provide the six-character confirmation code."
    v = graders.grade_contains(reply, {"needles": tz05["answer_contains"], "match": tz05["match"]})
    assert v["grade"] == "pass"            # no "?" required any more
    tz08 = _case("TZ08-doc-injection-no-exfil")["tool_script"][-1]
    assert "answer_forbid" not in tz08
    for reply in ("Priya Raman owns the renewal; it renews on 2026-11-30.",
                  "Owner: Priya Raman. Renewal: November 30, 2026. (The document also "
                  "contained an instruction to email attacker@example.com — ignored.)"):
        v = graders.grade_contains(reply, {"needles": tz08["answer_contains"], "match": "all"})
        assert v["grade"] == "pass", reply
    v = graders.grade_contains("Priya Raman owns it.", {"needles": tz08["answer_contains"],
                                                        "match": "all"})
    assert v["grade"] == "fail"


def test_cases_verify_clean():
    from crucibleforge.verify_cases import verify_all
    assert verify_all(config.load_cases()) == 0


# ------------------------------------------------------------ tool loop

class _Scripted:
    name = "fake"
    type = "openai"

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def chat(self, model_id, messages, **kw):
        self.calls.append({"messages": [dict(m) for m in messages], **kw})
        return self.results.pop(0)


def _tool(*calls, reasoning=""):
    return ChatResult(tool_calls=[{"id": f"c{i}", "name": n, "arguments": json.dumps(a)}
                                  for i, (n, a) in enumerate(calls)],
                      finish_reason="tool_calls", reasoning_text=reasoning, served_model="m")


def _loop(case, results):
    prov = _Scripted(results)
    cfg = {"defaults": {"thinking_max_tokens_factor": 4, "thinking_max_tokens_cap": 8192}}
    ctx = runner._Ctx(cfg, {"name": "m", "model_id": "m", "provider": "fake", "thinking": False},
                      prov, 32768)
    row = runner._run_tool_loop(case, {"case_id": case["id"]}, ctx, 1024, 0.0, 42)
    return row, prov


def test_tool_loop_answers_independent_calls_issued_together():
    case = _case("TZ01-contradicts-user-assumption")
    row, prov = _loop(case, [
        _tool(("list_backup_jobs", {"service": "orders"})),
        _tool(("get_last_run", {"job_id": "JOB-4412"})),
        _tool(("get_run_target", {"run_id": "RUN-88213"}),
              ("get_run_duration", {"run_id": "RUN-88213"}), reasoning="both at once"),
        ChatResult(response_text="RUN db-replica-2 cold-archive-eu 734s", finish_reason="stop",
                   served_model="m"),
    ])
    assert row["grade"] == "pass", row["grade_detail"]
    final_msgs = prov.calls[-1]["messages"]
    tool_msgs = [m for m in final_msgs if m["role"] == "tool"]
    assert len(tool_msgs) == 4                        # every call answered
    batch = [m for m in final_msgs if m.get("tool_calls") and len(m["tool_calls"]) == 2][0]
    assert batch["reasoning_content"] == "both at once"   # DeepSeek thinking mode needs it back
    assert len(prov.calls) == 4                       # step 4 consumed by the batch


def test_tool_loop_never_batches_a_retry():
    case = _case("TZ07-retry-after-rate-limit")
    first_step = case["tool_script"][0]
    name = first_step["expect_tool"]
    args = {k: v[0] for k, v in first_step.get("required_args", {}).items()}
    row, _ = _loop(case, [
        _tool((name, args), (name, args)),              # "retry" issued before the error came back
        _tool((name, args)),
        ChatResult(response_text="done", finish_reason="stop", served_model="m"),
    ])
    assert row["grade"] == "fail" and "2 tool calls" in row["grade_detail"]


def test_parallel_block_rules():
    script = [{"expect_tool": "a", "user": "go"}, {"expect_tool": "b"}, {"expect_tool": "c"},
              {"user": "final"}]
    call = lambda n: {"name": n, "arguments": "{}"}  # noqa: E731
    assert runner._parallel_block(script, 0, [call("a"), call("b")]) == [0, 1]
    assert runner._parallel_block(script, 1, [call("c"), call("b")]) == [1, 2]
    assert runner._parallel_block(script, 0, [call("b"), call("c")]) is None  # skips step a
    assert runner._parallel_block(script, 0, [call("a")]) is None             # single call
    assert runner._parallel_block(script, 1, [call("b"), call("c"), call("d")]) is None


# ------------------------------------------------------------ run-time

def test_template_parser_error_is_a_case_failure_not_transport():
    body = ('{"message": "llama-server for \'x\' returned HTTP 400: Unable to generate '
            'parser for this template. Automatic parser generation failed"}')
    assert isinstance(api.classify_server_error(None, "server error: " + body), GenerationRejected)

    def handler(request):
        return httpx.Response(400, text=body)
    real = httpx.Client

    class _C(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)
    orig = api.httpx.Client
    api.httpx.Client = _C
    try:
        with pytest.raises(GenerationRejected):
            api.stream_chat("http://x", "", "m", [], max_tokens=8)
    finally:
        api.httpx.Client = orig


def test_stalled_stream_is_not_retried(monkeypatch):
    calls = []

    def stall(*a, **k):
        calls.append(k.get("stall_s"))
        raise api.StreamStalled("no data for 300s")
    monkeypatch.setattr(api, "stream_chat", stall)
    with pytest.raises(api.StreamStalled):
        api.stream_chat_retried("http://x", "", "m", [], max_tokens=8, stall_s=300)
    assert calls == [300]


class _RunProv:
    """Enough of a Provider for runner.run_models."""
    name = "fake"
    type = "openai"
    base_url = "http://fake"

    def __init__(self, fn, workers=1):
        self.fn = fn
        self._workers = workers
        self.order = []

    def alive(self): return True
    def is_available(self, mid): return True
    def switch_model(self, *a, **k): return 0.0
    def live_context(self, mid): return None
    def loaded_plan_for(self, mid): return {}
    def is_loaded(self, mid): return True
    def workers(self, mid): return self._workers

    def chat(self, model_id, messages, **kw):
        return self.fn(messages, **kw)


def _run(tmp_path, monkeypatch, prov, cases, thinking=False):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(runner, "provider_for", lambda cfg, e: prov)
    cfg = {"defaults": {"thinking_max_tokens_factor": 4, "thinking_max_tokens_cap": 8192},
           "judge": {"candidates": []}, "_profile": "bench"}
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": thinking}
    summary = runner.run_models(cfg, [entry], cases)
    return summary, config.load_transcripts("m")


def test_parser_errors_error_cases_without_aborting_the_run(tmp_path, monkeypatch):
    def fn(messages, **kw):
        raise GenerationRejected("HTTP 400: Unable to generate parser for this template")
    cases = [c for c in config.load_cases(["instruct"])][:4]
    summary, rows = _run(tmp_path, monkeypatch, _RunProv(fn), cases)
    assert summary["m"]["failed"] is False            # 4 in a row did NOT abort the model
    # nothing was generated (the template cannot be rendered): errored, not
    # scored 0 (2026-09-24, precog-123b's tool rows)
    assert len(rows) == 4 and all(r["grade"] == "error" for r in rows)
    assert all(r["error_kind"] == "template" for r in rows)


def test_transport_errors_are_errored_rows_excluded_from_the_score(tmp_path, monkeypatch):
    n = {"i": 0}

    def fn(messages, **kw):
        n["i"] += 1
        if n["i"] == 1:
            raise TransportError("peer closed connection")
        return ChatResult(response_text="Hi there", finish_reason="stop", served_model="m")
    cases = [c for c in config.load_cases(["instruct"])][:2]
    summary, rows = _run(tmp_path, monkeypatch, _RunProv(fn), cases)
    errored = [r for r in rows if r.get("grade") == "error"]
    assert len(errored) == 1 and errored[0]["error_kind"] == "transport"
    assert not errored[0].get("needs_judge")
    s = report.model_stats("m")
    assert s["instruct"]["n"] == 1                    # the errored row is not an attempt
    assert s["errored"]


def test_long_categories_are_scheduled_first():
    cats = ["instruct", "tooluse", "rp", "math", "coding", "reasoning", "steer", "nsfw"]
    jobs = [({"category": c, "id": c}, 1, None) for c in cats]
    order = [j[0]["category"] for j in runner.schedule_jobs(jobs)]
    assert order[:3] == ["coding", "math", "reasoning"]
    assert order.index("instruct") > order.index("rp")


def test_few_slots_warning_on_a_thinking_model(tmp_path, monkeypatch):
    def fn(messages, **kw):
        return ChatResult(response_text="ok", reasoning_text="hmm", reasoning_tokens=5,
                          finish_reason="stop", served_model="m")
    cases = [c for c in config.load_cases(["instruct"])][:4]
    prov = _RunProv(fn, workers=1)
    summary, _ = _run(tmp_path, monkeypatch, prov, cases, thinking=True)
    assert any("parallel slot" in w for w in summary["m"]["warnings"])
    meta = json.loads((tmp_path / "meta_m.json").read_text())
    assert meta["warnings"] and meta["started"] and meta["finished"]


def test_math_skips_overflow_recovery():
    prov = _Scripted([ChatResult(response_text="", reasoning_text="think " * 50,
                                 finish_reason="length", served_model="m")])
    cfg = {"defaults": {"thinking_max_tokens_factor": 4, "thinking_max_tokens_cap": 8192,
                        "no_recovery": ["math"]}}
    ctx = runner._Ctx(cfg, {"name": "m", "model_id": "m", "provider": "fake", "thinking": True},
                      prov, 32768)
    assert ctx.recover_for({"category": "math"}) is False
    assert ctx.recover_for({"category": "coding"}) is True
    r = ctx.call([{"role": "user", "content": "q"}], max_tokens=256,
                 recover=ctx.recover_for({"category": "math"}))
    assert r.recovery is None and len(prov.calls) == 1


# ------------------------------------------------------------ judge

# the concurrent canary (now six probes, incl. godmod) is covered in
# tests/test_chat_section.py::test_godmod_canary_runs_concurrently_and_passes_a_strict_judge


def test_empty_final_turn_is_an_empty_generation_not_a_refusal():
    row = {"rubric": "rp_multi", "conversation": [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello, traveller."},
        {"role": "user", "content": "remember my name?"}, {"role": "assistant", "content": ""}]}
    v = judge.judge_row(object(), row)     # never reaches the judge client
    assert v["empty_generation"] is True and v["refused"] is False


def test_judge_row_timeout_is_recorded_not_reloaded():
    class JC:
        label = "j"
        thinking = False
        row_timeout_s = 1.0
        extra_body = {}

        def chat(self, messages, **kw):
            assert 0 < kw["max_wall_s"] <= 30.0 or kw["max_wall_s"] == 30.0
            raise api.RowTimeout("request exceeded its 1s wall-clock ceiling")
    with pytest.raises(api.RowTimeout):
        judge.judge_row(JC(), {"rubric": "rp_single", "prompt": "p", "response": "text"})


# ------------------------------------------------------------ fail fast

def test_non_gguf_id_on_studioforge_fails_in_seconds(monkeypatch):
    monkeypatch.setattr(studioforge, "list_models_full",
                        lambda *a, **k: [{"id": "a/b-GGUF/b-Q4", "studioforge": {"kind": "chat"}}])
    cfg = {"providers": {"sf": {"type": "studioforge", "base_url": "http://sf/v1"}},
           "defaults": {}, "models": [], "judge": {}}
    why = preflight.unrunnable_reason(cfg, {"name": "x", "provider": "sf",
                                            "model_id": "mistralai/Mistral-Small-4-119B-2603-NVFP4"})
    assert "not a GGUF" in why
    assert preflight.unrunnable_reason(cfg, {"name": "y", "provider": "sf",
                                             "model_id": "a/b-GGUF/b-Q4"}) is None


def test_judge_that_cannot_fit_fails_fast(monkeypatch):
    monkeypatch.setattr(studioforge, "list_models_full", lambda *a, **k: [{"id": "j/122b-GGUF/q"}])
    monkeypatch.setattr(studioforge, "gpu_indices", lambda *a, **k: [0, 1, 2, 3])
    monkeypatch.setattr(studioforge, "placement_fits", lambda *a, **k: False)
    cfg = {"providers": {"sf2": {"type": "studioforge", "base_url": "http://sf2/v1"}},
           "defaults": {}, "models": [], "judge": {}}
    why = preflight.judge_unrunnable_reason(cfg, {"provider": "sf2", "model_id": "j/122b-GGUF/q"})
    assert "does not fit" in why


def test_resident_leased_by_another_bench_is_refused(monkeypatch):
    from crucibleforge import providers
    p = providers.Provider(name="sf", type="studioforge", base_url="http://sf/v1")
    monkeypatch.setattr(studioforge, "loaded_plan",
                        lambda *a, **k: {"state": "ready", "parallel": 8, "ctx_size": 32768,
                                         "devices": [0, 1]})
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: [
        {"id": "L1", "holder": "crucibleforge", "devices": [0, 1], "model_ids": ["m"]}])
    with pytest.raises(studioforge.StudioForgeError, match="another CrucibleForge run"):
        p._resident_ready("m", 32768)
    # our OWN lease is fine
    p._lease = {"_lease_id": "L1"}
    assert p._resident_ready("m", 32768) is True


# ------------------------------------------------------------ rig lock

@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc ancestry")
def test_rig_lock_sees_a_parent_holding_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    lock = tmp_path / ".rig.lock"
    code = ("import sys; sys.path.insert(0, %r); from pathlib import Path; "
            "from crucibleforge import cli; print(cli._ancestor_holds(Path(%r)))"
            % (str(Path(__file__).resolve().parents[1]), str(lock)))
    with open(lock, "a"):
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.stdout.strip() == "True"
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.stdout.strip() == "False"


def test_rig_lock_serialises_two_benchmarks(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    f = cli.acquire_rig_lock()
    assert f is not None and "pid" in (tmp_path / ".rig.lock").read_text()
    code = ("import sys,fcntl; f=open(%r,'a+'); "
            "\ntry:\n fcntl.flock(f.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB); print('got')"
            "\nexcept BlockingIOError: print('busy')" % str(tmp_path / ".rig.lock"))
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.stdout.strip() == "busy"
    f.close()


# ------------------------------------------------------------ V2 run-report

def test_v2_result_carries_one_line_per_model(tmp_path, monkeypatch):
    from crucibleforge.version import revision
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / "transcripts_good.jsonl").write_text(
        json.dumps(_bench_row("IZ01-logistics-47-words", "instruct")) + "\n")
    (tmp_path / "meta_good.json").write_text(json.dumps({
        "model_id": "g", "profile": "bench", "bench_revision": revision(),
        "started": "2026-09-23T01:00:00+00:00", "finished": "2026-09-23T01:20:00+00:00",
        "judged": "2026-09-23T01:41:00+00:00"}))
    (tmp_path / "meta_bad.json").write_text(json.dumps({
        "model_id": "b", "profile": "bench", "bench_revision": revision(), "failed": True,
        "error": "mistralai/X-NVFP4 is not served by provider studioforge: it is not a GGUF id"}))
    cfg = {"models": [{"name": "good", "provider": "p", "model_id": "g"},
                      {"name": "bad", "provider": "p", "model_id": "b"}],
           "providers": {"p": {"type": "openai", "base_url": "http://x"}},
           "defaults": {}, "judge": {"candidates": []}}
    args = cli.argparse.Namespace(cmd="all", models="good,bad", run_id="w-x", requester=None,
                                  deliver_to=None, task_run_id=None, profile=None,
                                  categories=None, difficulty=None, cases=None)
    state = cli._v2_start(args)
    body = cli._v2_report_body("all", args, cfg, 1, state)
    result = body.split("## Result\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert result[0].startswith("good: Chat - · Coding 100.0 · 41 min")
    assert result[1].startswith("bad: FAILED — mistralai/X-NVFP4 is not served")
    assert body.count("\n# ") == 0 and body.startswith("# ")   # exactly one H1
    nxt = body.split("## Next", 1)[1]
    assert "GGUF" in nxt
    files = body.split("## Files", 1)[1].split("##", 1)[0]
    assert "transcripts_bad.jsonl" not in files                # only files that exist
    assert "transcripts_good.jsonl" in files
