"""Review 3 of the 4.5.1 limits work (2026-11-25): the rig's wide window falls back instead of aborting (fix 2), a
hosted provider states and enforces its output maximum and a refusal is retried once at the stated limit (fix 3),
a re-run failing on infrastructure is not owed without bound, an interrupted CQ unit keeps its finished repeats,
the plan prices owed rows, and a bare closer that belongs to the file is left alone."""
from __future__ import annotations

import pytest

from crucibleforge import attempts45, cli, codebank, edittol, providers, report, runner
from crucibleforge.api import ChatResult, RequestRejected

B = {"factor": 10, "floor": 32768, "cap": 57344, "thinker_context": 81920,
     "case_budgets": {"CQ1-*": 131072}}


# ---- fix 2: the wide window -------------------------------------------------------------------------------------
def test_window_fallbacks_go_down_from_the_wanted_window_to_the_floor():
    assert runner.window_fallbacks(139264) == [118784, 102400, 86016, runner.WIDE_FALLBACK_FLOOR]
    assert runner.window_fallbacks(runner.WIDE_FALLBACK_FLOOR) == []


class _Rig:
    name, type = "rig", "studioforge"

    def __init__(self, refuse_above):
        self.refuse_above, self.loads = refuse_above, []

    def switch_model(self, model_id, ctx):
        self.loads.append(ctx)
        if ctx > self.refuse_above:
            raise RuntimeError(f"load refused: not enough memory for ctx {ctx}")
        return 1.0


CQ = [{"id": "CQ1-format", "category": "coding", "prompt": "x", "ladder_budget": 65536}]


def test_a_refused_wide_load_falls_back_to_the_largest_window_the_rig_grants():
    cfg = {"defaults": {"budgets_451": B}}
    rig = _Rig(100000)
    w, s = runner._wide_load_fallback(cfg, rig, "m", 139264, CQ, None, RuntimeError("refused"))
    assert (w, s) == (86016, 1.0) and rig.loads == [118784, 102400, 86016]


def test_a_narrow_round_or_a_rung_re_raises_as_before():
    cfg = {"defaults": {"budgets_451": B}}
    err = RuntimeError("refused")
    with pytest.raises(RuntimeError):
        runner._wide_load_fallback(cfg, _Rig(10**6), "m", 32768, [{"id": "I1", "category": "instruct"}], None, err)
    with pytest.raises(RuntimeError):
        runner._wide_load_fallback(cfg, _Rig(10**6), "m", 139264, CQ, 131072, err)
    with pytest.raises(RuntimeError):
        runner._wide_load_fallback(cfg, _Rig(1000), "m", 139264, CQ, None, err)      # nothing fits: the error


class _C:
    b451, rung, max_output = B, None, 0

    def __init__(self, ctx_len):
        self.ctx_len = ctx_len

    def budget(self, mt):
        return 33536


def test_a_cq_row_on_a_narrower_window_runs_clamped_and_is_not_owed_again(monkeypatch):
    monkeypatch.setattr(runner, "_prompt_tokens", lambda m: 1000)
    st = runner.budget_stamp(_C(86016), CQ[0], 768)
    granted = 86016 - 1000 - runner.BANK_WINDOW_SLACK
    assert st["budget_clamp"] == {"route_max": "rig window", "wanted": 131072, "granted": granted, "window": 86016}
    assert st["budget_rev"]
    assert "budget_clamp" not in runner.budget_stamp(_C(139264), CQ[0], 768)      # the whole budget fits
    row = {"case_id": "CQ1-format", "repeat": 1, "category": "coding", "finish_reason": "length", "response": "x",
           "grade": "fail", "score": 0.0, "max_tokens_sent": granted, **st}
    assert attempts45.superseded(row) is None and not attempts45.is_infra(row)


# ---- fix 3: hosted output maximum --------------------------------------------------------------------------------
def _hosted(url, **k):
    p = providers.Provider(name="h", type="openai", base_url=url, **k)
    p._route_max_read = True                            # no listing call
    return p


def test_every_hosted_provider_can_state_its_output_maximum():
    assert _hosted("https://api.deepseek.com/v1").route_max_output("deepseek-flash") == 393216
    p = _hosted("https://api.minimax.io/v1")
    assert p.route_max_output("MiniMax-M3.1-Flash") == 524288 and p.route_max_source() == "documented default"
    p = _hosted("https://api.deepseek.com/v1", max_output_tokens={"deepseek-flash": 65536})
    assert p.route_max_output("deepseek-flash") == 65536 and p.route_max_source() == "provider config"
    p = _hosted("https://llm.example.invalid/v1")
    p._route_max["m"] = 32000
    assert p.route_max_output("m") == 32000 and p.route_max_source() == "provider listing"
    assert _hosted("https://llm.example.invalid/v1").route_max_output("m") is None


def test_an_openrouter_id_without_a_route_says_so(caplog):
    p = _hosted("https://openrouter.ai/api/v1")
    with caplog.at_level("WARNING"):
        assert p.route_max_output("vendor/nope") is None
    assert "budgets are NOT clamped" in caplog.text


@pytest.mark.parametrize("text,asked,want", [
    ("max_tokens is too large: 131072. This model supports at most 65536 completion tokens", 131072, 65536),
    ("Invalid max_tokens value, the valid range of max_tokens is [1, 8192]", 131072, 8192),
    ("max_completion_tokens must be less than or equal to 64,000", 131072, 64000),
    ("the request exceeds the available context size", 131072, None),            # a context refusal, not this
    ("upstream error 503", 131072, None),
])
def test_the_limit_is_read_from_the_refusal(text, asked, want):
    assert runner.output_limit_from_error(text, asked) == want


class _Refusing(providers.Provider):
    def __init__(self):
        super().__init__(name="api", type="openai", base_url="https://llm.example.invalid/v1")
        self._route_max_read, self.sent = True, []

    def chat(self, model_id, messages, **kw):
        self.sent.append(kw.get("max_tokens"))
        if kw["max_tokens"] > 65536:
            raise RequestRejected(400, "max_tokens is too large: 131072. This model supports at most 65536 "
                                       "completion tokens")
        return ChatResult(response_text="ok", finish_reason="stop")


def test_a_refused_budget_is_retried_once_at_the_stated_limit_and_kept():
    p = _Refusing()
    ctx = runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m"}, p, None)
    r = ctx._chat([{"role": "user", "content": "x"}], max_tokens=131072)
    assert r.response_text == "ok" and p.sent == [131072, 65536]
    assert ctx.output_refusal == {"asked": 131072, "limit": 65536} and ctx.max_output == 65536
    ctx._chat([{"role": "user", "content": "y"}], max_tokens=131072)
    assert p.sent[-1] == 65536                                                     # later calls clamp first


# ---- notes --------------------------------------------------------------------------------------------------------
def _cut(ts, **k):
    return {"case_id": "C1", "repeat": 1, "category": "coding", "finish_reason": "length", "response": "x",
            "grade": "fail", "score": 0.0, "ts": ts, "max_tokens_sent": 16384,
            "profile_switches": {}, **k}


def test_a_rerun_failing_on_infrastructure_twice_is_no_longer_owed(monkeypatch):
    monkeypatch.setattr(attempts45, "superseded", lambda r: "budget raised" if not r.get("budget_rev")
                        and r.get("finish_reason") == "length" else None)
    err = {"case_id": "C1", "repeat": 1, "grade": "error", "error_kind": "transport", "error": "HTTP 503"}
    rows = [_cut("2026-11-20T00:00:00"), {**err, "ts": "2026-11-26T00:00:00"}]
    assert attempts45.rerun_gave_up(rows) == set()
    rows.append({**err, "ts": "2026-11-26T01:00:00"})
    assert attempts45.rerun_gave_up(rows) == {("C1", 1)}
    monkeypatch.setattr(cli, "_resume_rows", lambda label, cfg: rows)
    monkeypatch.setattr("crucibleforge.config.load_transcripts", lambda label: rows)
    assert cli._first_reply_jobs("m", {"defaults": {}}, [{"id": "C1", "category": "coding"}]) == set()
    L = report.losses45(rows, {"code": {"writes": {"max": 4, "cases": ["C1"]}}})
    assert L["code.writes"]["rerun_failed"] == 1


def test_an_interrupted_cq_unit_keeps_its_finished_repeats(monkeypatch):
    from crucibleforge import budgets451
    monkeypatch.setattr(attempts45, "superseded", lambda r: None if r.get("budget_rev") else "budget raised")
    monkeypatch.setattr(budgets451, "case_budget", lambda b, cid: 131072)
    monkeypatch.setattr("crucibleforge.config.load_transcripts", lambda label: [])
    monkeypatch.setattr(cli, "repeats_for", lambda *a, **k: 2, raising=False)
    rows = [{"case_id": "CQ1-x", "repeat": 1, "category": "coding", "finish_reason": "stop", "response": "x",
             "grade": "pass", "score": 1.0, "budget_rev": "b451-1", "ts": "2026-11-26"},
            _cut("2026-11-20", case_id="CQ1-x", repeat=2)]
    import crucibleforge.config as C
    monkeypatch.setattr(C, "repeats_for", lambda *a, **k: 2)
    got = cli._first_reply_jobs("m", {"defaults": {}}, [{"id": "CQ1-x", "category": "coding"}], rows=rows)
    assert got == {(None, "CQ1-x", 2)}                                             # repeat 1 is not re-done


def test_the_plan_prices_owed_rows():
    line = cli.owed_price_line([({"id": "C1", "prompt": "x" * 300}, 65536)], {"input": 1.0, "output": 2.0},
                               {"thinking": True})
    assert line.startswith("  owed price: 1 row(s), ") and "$" in line and "at most" in line
    assert "no price" in cli.owed_price_line([({"id": "C1"}, 1000)], None, {})


# ---- edittol: a bare marker line that belongs to the file --------------------------------------------------------
@pytest.mark.parametrize("text", [
    "f.md\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>\nmore\n>>>>>>> REPLACE\n",
    "a.txt\n<<<<<<< SEARCH\nx\n=======\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>>\n>>>>>>> REPLACE\n",
])
def test_a_bare_line_before_a_proper_closer_is_replacement_text(text):
    assert edittol.rewrite(text) == (text, 0)


def test_the_last_bare_candidate_closes_a_block_with_no_proper_closer():
    text = "a.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>\ny = 3\n>>>>>>>\n"
    out, n = edittol.rewrite(text)
    assert n == 1 and out.endswith(">>>>>\ny = 3\n>>>>>>> REPLACE\n")
    edits, _ = codebank.parse_edits(out)
    assert edits[0]["replace"] == "x = 2\n>>>>>\ny = 3\n"
