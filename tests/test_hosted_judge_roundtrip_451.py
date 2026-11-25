"""4.5.1: every judge caller reaches a hosted judge through a real HTTP round trip and books its calls as judge spend.

The second hosted end-to-end run (2026-11-24) lost the role-play session judge and the pack judge: `all` resolved the
hosted judge to its own provider entry ``<provider>~judge`` for the first judge phase, a later phase guarded the
already-resolved judge again against the config as loaded and got ``<provider>~judge~judge`` with an empty entry
(no URL, 372 failed calls). Unit tests with fake clients never send a request, so nothing caught it. Here a tiny
OpenAI-shaped server on 127.0.0.1 stands in for the hosted API; the provider entry says ``hosted: true`` (a loopback
server is otherwise local). No outside network, no rig."""
from __future__ import annotations

import json
import threading
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from crucibleforge import cli, config, judge, judge45, profiles, providers, rpladder_run, spend

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]

JUDGE_ID = "vendor/hosted-judge"


class _Fake(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": JUDGE_ID}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        type(self).seen.append({"path": self.path, "model": req.get("model")})
        answer = json.dumps({"better": "A", "reason": "r", "cls": "EXPLICIT", "quote": "", "refused": False,
                             "position": False})
        chunks = [{"model": JUDGE_ID, "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": None}]},
                  {"model": JUDGE_ID, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}]
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())


@pytest.fixture
def server():
    _Fake.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def prof():
    return profiles.load_profile("bench45")


def _cfg(url):
    return {"providers": {"router": {"type": "openai", "base_url": url, "hosted": True, "concurrency": 2,
                                     "api_key": "test-key"}},
            "models": [], "defaults": {},
            "judge": {"samples": 1, "candidates": [
                {"name": "rj", "provider": "router", "model_id": JUDGE_ID,
                 "price": {"input": 1.0, "output": 1.0}}]}}


def _as_all_does(cfg, prof):
    """`all`: the first judge phase guards the judge; the role-play session judge, the ladder rounds' pack judge and
    judge-check guard the SAME args again against the config as loaded."""
    providers._CACHE.clear()
    args = Namespace(judge="rj")
    first = cli._guard_45_judge(args, cfg, prof)
    again = cli._guard_45_judge(args, cfg, prof)
    assert args.judge["provider"] == "router~judge"                 # never router~judge~judge
    assert again["providers"]["router~judge"]["base_url"] == cfg["providers"]["router"]["base_url"]
    return args, first, again


def _booked(before):
    calls, _usd = spend.judge_totals()
    return calls - before


def test_a_judge_alias_resolves_to_the_users_provider_entry(server):
    cfg = _cfg(server)
    assert config.provider_of(cfg, "router~judge")["base_url"] == server
    assert config.provider_of(cfg, "router~judge~judge")["base_url"] == server
    with pytest.raises(config.ConfigError):
        config.provider_of(cfg, "nowhere~judge")
    assert judge45.base_provider("router~judge~judge") == "router"


def test_hosted_true_marks_a_loopback_server_hosted(server, prof):
    cfg = _cfg(server)
    assert judge45.provider_hosted(cfg, "router") is True
    assert providers.get_provider(cfg, "router").is_hosted is True
    del cfg["providers"]["router"]["hosted"]
    providers._CACHE.clear()
    assert judge45.provider_hosted(cfg, "router") is False                # a local server by its address
    assert providers.get_provider(cfg, "router").is_hosted is False


CALLERS = ("standard judge", "long scenes", "ladder-judge", "pack judge", "NSFW+ classing", "judge-check")


@pytest.mark.parametrize("caller", CALLERS)
def test_every_judge_caller_reaches_the_hosted_judge_and_books_judge_spend(caller, server, prof, tmp_path,
                                                                            monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(spend, "outside_total_usd", lambda: 0.0)
    monkeypatch.setenv(spend.LEDGER_ENV, str(tmp_path / "ledger.jsonl"))
    spend.reset()
    cfg = _cfg(server)
    args, first, again = _as_all_does(cfg, prof)
    before = spend.judge_totals()[0]
    msg = [{"role": "user", "content": "Pick the better of two made-up scenes about a lighthouse."}]
    if caller in ("standard judge", "long scenes"):
        # judge.run_judge's client: the first phase's config and the resolved judge
        jc = judge._load_judge(first, set(), rows=1, samples=1, override=args.judge)
        row = {"case_id": "C", "rubric": "rp_single", "needs_judge": True, "prompt": "p",
               "response": "A calm scene at the lighthouse." * (400 if caller == "long scenes" else 1),
               "finish_reason": "stop", "tool_calls": None, "model_label": "m"}
        judge.judge_row(jc, row)
    elif caller == "ladder-judge":
        # cmd_ladder_judge guards the already-resolved judge again (the 2026-11-24 failure: router~judge~judge)
        jc = judge._load_judge(again, set(), rows=1, samples=1, override=args.judge)
        stats: dict = {}
        rpladder_run.make_judge_fn(jc, stats)([{"key": "k", "messages": msg}])
        assert not stats.get("errors"), stats
    elif caller == "pack judge":
        # the ladder rounds' judge phase: cmd_judge guards again, then run_judge loads
        jc = judge._load_judge(again, set(), rows=1, samples=1, override=args.judge)
        jc.chat(msg)
    elif caller == "NSFW+ classing":
        import test_judge45_part1_20261121 as P1
        res = P1._folder(tmp_path / "f", P1.std_rows(variants=("write",), n=1))
        jc = judge._load_judge(first, set(), rows=1, samples=1, override=args.judge)
        rd = judge45.judge_reader(jc.model_id, provider=jc.provider.name, provider_type=jc.provider.type,
                                  base_url=jc.provider.base_url, hosted=jc.provider.is_hosted)
        assert rd.hosted
        judge45.judge_model(res, P1.LABEL, prof, rd, judge45.client_chat(jc))
    else:
        a = Namespace(judge="rj", profile="bench45", show=False, force_evict=False, cmd="judge-check")
        monkeypatch.setattr(cli, "_apply_profile_arg", lambda ar, c: (c, [], prof))
        monkeypatch.setattr(cli, "results_dir", lambda: tmp_path)
        assert cli.cmd_judge_check(a, cfg) == 0
    assert _Fake.seen, f"{caller}: no request reached the judge's server"
    assert all(s["path"].endswith("/chat/completions") and s["model"] == JUDGE_ID for s in _Fake.seen)
    assert _booked(before) >= 1, f"{caller}: the calls were not booked as judge spend"
    led = [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
           if '"cost_usd"' in x]
    assert led and all(x.get("kind") == "judge" for x in led), f"{caller}: ledger lines {led[:2]}"


def test_every_judge_callers_spend_is_in_the_printed_total(server, prof, tmp_path, monkeypatch, capsys):
    """Third Docker run (2026-11-25): "judge cost: $0.0343 for 732 call(s)" while the provider balance fell ~$0.18.
    The role-play session judge (and every later phase) works on a COPY of the guarded config ({**cfg, ...}); the
    judge's price was set on one cached Provider object only, so the copy's new Provider priced nothing and its calls
    were counted at $0. Each caller below works on its own copy, as the real phases do; each one's spend must show
    in the total `all` prints last."""
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(spend, "outside_total_usd", lambda: 0.0)
    monkeypatch.setenv(spend.LEDGER_ENV, str(tmp_path / "ledger.jsonl"))
    spend.reset()
    spend.forget_judges()
    cfg = _cfg(server)          # the judge candidate carries the price (models.example.yaml's qwen27-judge-openrouter)
    providers._CACHE.clear()
    args = Namespace(judge="rj")
    first = cli._guard_45_judge(args, cfg, prof)
    msg = [{"role": "user", "content": "Pick the better of two made-up scenes about a lighthouse."}]
    deltas = {}
    for caller in CALLERS:
        c = {**cli._guard_45_judge(args, cfg, prof), "_profile": "bench45"}      # a phase's own copy
        usd0 = spend.judge_totals()[1]
        if caller in ("standard judge", "long scenes"):
            jc = judge._load_judge(c if caller == "long scenes" else first, set(), rows=1, samples=1,
                                   override=args.judge)
            row = {"case_id": "C", "rubric": "rp_single", "needs_judge": True, "prompt": "p",
                   "response": "A calm scene at the lighthouse.", "finish_reason": "stop", "tool_calls": None,
                   "model_label": "m"}
            judge.judge_row(jc, row)
        elif caller == "ladder-judge":
            jc = judge._load_judge(c, set(), rows=1, samples=1, override=args.judge)
            stats: dict = {}
            rpladder_run.make_judge_fn(jc, stats)([{"key": "k", "messages": msg}])
            assert not stats.get("errors"), stats
        elif caller == "pack judge":
            judge._load_judge(c, set(), rows=1, samples=1, override=args.judge).chat(msg)
        elif caller == "NSFW+ classing":
            import test_judge45_part1_20261121 as P1
            res = P1._folder(tmp_path / "f", P1.std_rows(variants=("write",), n=1))
            jc = judge._load_judge(c, set(), rows=1, samples=1, override=args.judge)
            rd = judge45.judge_reader(jc.model_id, provider=jc.provider.name, provider_type=jc.provider.type,
                                      base_url=jc.provider.base_url, hosted=jc.provider.is_hosted)
            judge45.judge_model(res, P1.LABEL, prof, rd, judge45.client_chat(jc))
        else:
            a = Namespace(judge="rj", profile="bench45", show=False, force_evict=False, cmd="judge-check")
            monkeypatch.setattr(cli, "_apply_profile_arg", lambda ar, cc: ({**cc}, [], prof))
            monkeypatch.setattr(cli, "results_dir", lambda: tmp_path)
            assert cli.cmd_judge_check(a, cfg) == 0
        deltas[caller] = spend.judge_totals()[1] - usd0
    assert all(d > 0 for d in deltas.values()), f"callers booked at $0: {[k for k, d in deltas.items() if d <= 0]}"
    line = cli._judge_cost_line(args, whole_run=True)
    calls, usd = spend.judge_totals()
    assert usd == pytest.approx(sum(deltas.values()))
    assert line == (f"judge cost, whole run (every judge phase): ${usd:.4f} for {calls} call(s) to {JUDGE_ID} on "
                    "router (billed apart: not part of any model's Run cost)")


def test_the_explicit_check_failure_is_one_summary_line():
    import logging
    with cli._ExplicitCheckWatch() as w:
        logging.getLogger("crucibleforge.judge").warning(
            "%s — judging the non-NSFW rows only; %d NSFW row(s) stay pending for an NSFW-capable judge",
            "canary FAILED (NSFW probes only): judge rated clearly-sexual text explicitness=3 (<5)", 76)
    assert w.line() == ("judge: the judge failed its explicit-text check: NSFW+ was not judged; MakoBench's "
                        "published NSFW+ uses the local Q8 judge")
    with cli._ExplicitCheckWatch() as w2:
        logging.getLogger("crucibleforge.judge").warning("canary passed")
    assert w2.line() is None


# ------------------------------------------------------------------ a plain re-run resumes the standard jobs
def test_a_plain_rerun_does_not_redraw_stored_standard_jobs(monkeypatch, prof):
    cases = [{"id": "A", "category": "reasoning"}, {"id": "B", "category": "reasoning"},
             {"id": "CH", "category": "chat", "chain_spec": {"x": 1}}]
    monkeypatch.setattr(cli, "_first_reply_rule", lambda p: True)
    monkeypatch.setattr("crucibleforge.config.repeats_for", lambda cfg, cat, smoke, c=None: 1)
    stored = {"m": {(None, "B", 1)}, "new": {(None, "A", 1), (None, "B", 1)}}
    monkeypatch.setattr(cli, "_first_reply_jobs", lambda label, cfg, cs, smoke, **kw: set(stored[label]))
    monkeypatch.setattr(cli, "_resume_rows", lambda label, cfg: [])
    monkeypatch.setattr(cli, "_stored_mismatch", lambda cfg, e, rows, smoke: None)   # test_resume_match_451
    calls = []

    def run_models(cfg, entries, cs, **kw):
        calls.append(([e["name"] for e in entries], sorted(c["id"] for c in cs), kw.get("jobs_by_label")))
        return {e["name"]: {"rows": 0} for e in entries}
    entries = [{"name": "m"}, {"name": "new"}]
    args = Namespace(smoke=False, fresh=False, skip_conformance=False)
    cli._run_selected({}, entries, cases, prof, args, None, run_models)
    std, chains = calls
    assert std[0] == ["m", "new"] and std[1] == ["A", "B"]
    assert std[2] == {"m": {(None, "B", 1)}}          # m: only its owed job; "new" has no stored row: a full run
    assert chains[1] == ["CH"]                         # chains resume from their stored attempts
    calls.clear()
    cli._run_selected({}, entries, cases, prof, Namespace(smoke=False, fresh=True, skip_conformance=False), None,
                      run_models)
    assert len(calls) == 1 and calls[0][2] is None     # --fresh: the rows were archived, a full run
