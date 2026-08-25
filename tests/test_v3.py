"""v3 additions: providers/config schema, legacy upgrade, new graders,
reference judge rubric, long-context generator, case verifier, OpenClaw
import, GUI markdown + auth. No network."""
import json
import threading
from pathlib import Path

import pytest
import yaml

from crucibleforge import config, graders, judge, longctx_gen, providers, verify_cases
from crucibleforge.api import ChatResult
from crucibleforge.config import ConfigError


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    providers.clear_cache()
    yield
    providers.clear_cache()


# ------------------------------------------------------------ config v3

def test_example_config_validates():
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    assert set(cfg["providers"]) >= {"lmstudio", "deepseek", "openrouter"}
    assert all(m["provider"] in cfg["providers"] for m in cfg["models"])
    assert cfg["judge"]["candidates"][0]["provider"] in cfg["providers"]


def test_legacy_v2_shape_upgrades():
    v2 = {"endpoint": {"base_url": "http://l/v1", "api_key": "k",
                       "rig": {"base_url": "http://b/v1", "api_key": ""}},
          "defaults": {}, "judge": {"candidates": [
              {"model_id": "pub/repo/file", "thinking": True},
              {"model_id": "local-key"}]},
          "models": [{"name": "a", "model_id": "pub/repo/file",
                      "studioforge": {"reasoning_format": "deepseek"}},
                     {"name": "b", "model_id": "google/gemma"}]}
    cfg = config.upgrade_legacy(v2)
    config.validate_config(cfg)
    assert cfg["providers"]["lmstudio"]["type"] == "lmstudio"
    assert cfg["providers"]["studioforge"]["base_url"] == "http://b/v1"
    a, b = cfg["models"]
    assert a["provider"] == "studioforge" and a["extra_body"] == {"reasoning_format": "deepseek"}
    assert "studioforge" not in a
    assert b["provider"] == "lmstudio"
    assert cfg["judge"]["candidates"][0]["provider"] == "studioforge"
    assert cfg["judge"]["candidates"][1]["provider"] == "lmstudio"
    # idempotent
    assert config.upgrade_legacy(cfg) is cfg


def test_literal_remote_key_rejected():
    cfg = {"providers": {"r": {"type": "openai", "base_url": "http://x",
                               "api_key": "a-literal-secret-value-not-an-env-ref"}},
           "defaults": {}, "judge": {"candidates": []}, "models": []}
    with pytest.raises(ConfigError):
        config.validate_config(cfg)
    cfg["providers"]["r"]["api_key"] = "${MY_KEY}"
    config.validate_config(cfg)  # env placeholder is fine


def test_unknown_provider_on_model_rejected():
    cfg = {"providers": {"r": {"type": "openai", "base_url": "http://x"}},
           "defaults": {}, "judge": {"candidates": []},
           "models": [{"name": "m", "model_id": "x", "provider": "nope"}]}
    with pytest.raises(ConfigError):
        config.validate_config(cfg)


def test_provider_resolves_env_key(monkeypatch):
    monkeypatch.setenv("TEST_CRUCIBLEFORGE_KEY", "abc123")
    cfg = {"providers": {"r": {"type": "openai", "base_url": "http://x/v1/",
                               "api_key_env": "TEST_CRUCIBLEFORGE_KEY", "concurrency": 3}},
           "defaults": {}, "judge": {"candidates": []}, "models": []}
    p = providers.get_provider(cfg, "r")
    assert p.api_key == "abc123" and p.concurrency == 3
    assert p.base_url == "http://x/v1"          # trailing slash stripped
    assert p.verify_model is False              # openai default: relaxed
    q = providers.get_provider({"providers": {"l": {"type": "lmstudio", "base_url": "u"}},
                                "defaults": {}, "judge": {}, "models": []}, "l")
    assert q.verify_model is True


def test_cost_from_price():
    entry = {"price": {"input": 1.0, "output": 2.0}}
    assert providers.cost_usd(entry, 1_000_000, 500_000) == pytest.approx(2.0)
    assert providers.cost_usd({}, 10, 10) is None


def test_results_dir_follows_config(tmp_path, monkeypatch):
    monkeypatch.delenv("CRUCIBLEFORGE_RESULTS", raising=False)
    dst = tmp_path / "models.yaml"
    dst.write_text(config.EXAMPLE_CONFIG_PATH.read_text())
    config.load_config(dst)
    assert config.results_dir() == tmp_path / "results"


# -------------------------------------------------------------- graders

def test_exact_grader_normalizes():
    r = ChatResult(response_text="Reasoning...\nAnswer: B, D, A")
    assert graders.grade(r, {"grader": "exact", "grader_config": {"answer": "b d a"}})["grade"] == "pass"
    assert graders.grade(r, {"grader": "exact", "grader_config": {"answer": "a b d"}})["grade"] == "fail"
    r2 = ChatResult(response_text="Answer: 10110")
    assert graders.grade(r2, {"grader": "exact", "grader_config": {"answers": ["0b10110", "10110"]}})["grade"] == "pass"


def test_contains_forbid():
    r = ChatResult(response_text="Answer: Alice and Bob")
    v = graders.grade(r, {"grader": "contains", "grader_config": {
        "needles": ["alice"], "answer_line": True, "forbid": ["bob"]}})
    assert v["grade"] == "fail" and "forbidden" in v["detail"]


def test_reference_grader_defers_to_judge():
    r = ChatResult(response_text="It is the second one, obviously.")
    v = graders.grade(r, {"grader": "reference", "grader_config": {
        "reference": "option 2", "needles": ["option 2"]}})
    assert v["grade"] == "pending" and v["needs_judge"] and v["reference"] == "option 2"
    r2 = ChatResult(response_text="Answer: option 2")
    assert graders.grade(r2, {"grader": "reference", "grader_config": {
        "reference": "option 2", "needles": ["option 2"]}})["grade"] == "pass"


def test_reference_rubric_parses_and_applies():
    v = judge.parse_verdict('{"correct": true, "note": "same value"}', "reference")
    assert v == {"correct": True, "note": "same value"}
    row = {"rubric": "reference", "grade": "pending"}
    judge._apply_reference_grade(row, {"judge_failed": False, "scores": v})
    assert row["grade"] == "pass"
    judge._apply_reference_grade(row, {"judge_failed": False,
                                       "scores": {"correct": False, "note": "different set"}})
    assert row["grade"] == "fail" and "different set" in row["grade_detail"]
    # a JUDGE failure leaves the row pending for a re-judge (not a model fail)
    row["judge"] = {"judge_failed": True}
    judge._apply_reference_grade(row, {"judge_failed": True, "judge_raw": "garbage"})
    assert row["grade"] == "pending" and "judge" not in row
    # an EMPTY model answer is the model's fail
    judge._apply_reference_grade(row, {"judge_failed": True, "empty_generation": True})
    assert row["grade"] == "fail"


def test_reference_judge_input_shows_reference():
    row = {"rubric": "reference", "prompt": "Q?", "reference": "42", "response": "forty-two"}
    rubric, text = judge.build_judge_input(row)
    assert rubric == "reference"
    assert "## Reference Answer\n42" in text and "forty-two" in text
    assert "BEGIN MODEL OUTPUT" in text  # fenced


def test_select_judge_override_and_under_test():
    cfg = {"providers": {"p": {"type": "openai", "base_url": "http://x"}},
           "defaults": {}, "models": [],
           "judge": {"candidates": [{"provider": "p", "model_id": "j1"},
                                    {"provider": "p", "model_id": "j2"}]}}
    assert judge.select_judge(cfg, set(), override="p:j2")["model_id"] == "j2"
    assert judge.select_judge(cfg, set(), override="j1")["model_id"] == "j1"
    with pytest.raises(judge.JudgeError):
        judge.select_judge(cfg, {"j1"}, override="j1")
    with pytest.raises(judge.JudgeError):
        judge.select_judge(cfg, set(), override="nonexistent")


# ------------------------------------------------------------ longctx gen

def test_longctx_generator_deterministic_and_needled():
    spec = {"type": "niah", "tokens": 3000, "depth": 0.5, "seed": 11,
            "needle": "The code is red-fox-77.", "question": "What is the code?"}
    a, b = longctx_gen.build(spec), longctx_gen.build(spec)
    assert a == b and "red-fox-77" in a and a.rstrip().endswith("What is the code?")
    assert 9000 < len(a) < 16000
    spec2 = dict(spec, seed=12)
    assert longctx_gen.build(spec2) != a


def test_longctx_count_and_multikey():
    p = longctx_gen.build({"type": "count", "tokens": 2000, "seed": 3, "count": 5,
                           "marker": "MARK X.", "question": "How many?"})
    assert p.count("MARK X.") == 5
    p2 = longctx_gen.build({"type": "multikey", "tokens": 2000, "seed": 4,
                            "needles": ["A is 1.", "B is 2.", "C is 3."],
                            "depths": [0.1, 0.5, 0.9], "question": "B?"})
    assert p2.index("A is 1.") < p2.index("B is 2.") < p2.index("C is 3.")


def test_load_cases_materializes_generated_and_min_context():
    cases = config.load_cases(["longctx"])
    gen = [c for c in cases if c.get("generator")]
    assert gen, "expected generated long-context cases"
    for c in gen:
        assert c["prompt"] and c["min_context"] > c["generator"]["tokens"]


# ---------------------------------------------------------- case verifier

def test_case_set_verifies_clean():
    cases = config.load_cases()
    bad = [(c["id"], verify_cases._check(c)) for c in cases if verify_cases._check(c)]
    assert not bad, bad


def test_verifier_catches_bad_reference_and_wrong_gold():
    bad_ref = {"id": "x", "category": "coding", "max_tokens": 10, "prompt": "p",
               "grader": "python_exec", "grader_config": {"tests": "assert f(1) == 2"},
               "reference": "def f(x):\n    return x"}
    assert any("FAILS" in e for e in verify_cases._check(bad_ref))
    wrong = {"id": "y", "category": "math", "max_tokens": 10, "prompt": "p",
             "grader": "numeric", "grader_config": {"answer": 5},
             "verify": {"python": "2+2"}}
    assert any("derived" in e for e in verify_cases._check(wrong))


def test_hard_tier_is_substantial():
    cases = config.load_cases()
    hard = [c for c in cases if c.get("difficulty") == "hard"]
    assert len(hard) >= 60
    for cat in ("math", "coding", "reasoning", "tooluse", "longctx"):
        assert sum(1 for c in hard if c["category"] == cat) >= 8, cat


# --------------------------------------------------------- openclaw import

def test_openclaw_import_maps_env_keys_and_prices(tmp_path):
    from crucibleforge.openclaw_import import import_openclaw
    oc = {"models": {"providers": {
        "deepseek": {"baseUrl": "https://api.deepseek.com/v1", "api": "openai-completions",
                     "apiKey": "${DEEPSEEK_API_KEY}",
                     "models": [{"id": "deepseek-v4-flash", "cost": {"input": 0.14, "output": 0.28},
                                 "contextWindow": 1000000, "reasoning": True}]},
        "local": {"baseUrl": "http://127.0.0.1:1234/v1", "api": "openai-completions",
                  "apiKey": "local", "models": [{"id": "x"}]},
        "leaky": {"baseUrl": "https://api.example.com/v1", "api": "openai-completions",
                  "apiKey": "a-literal-secret-value-not-an-env-ref", "models": []},
    }}}
    p = tmp_path / "openclaw.json"
    p.write_text(json.dumps(oc))
    cfg = {"providers": {}, "models": [], "judge": {"candidates": []}, "defaults": {}}
    added = import_openclaw(cfg, p)
    assert "deepseek" in added["providers"] and "local" not in cfg["providers"]
    assert cfg["providers"]["deepseek"]["api_key_env"] == "DEEPSEEK_API_KEY"
    m = cfg["models"][0]
    assert m["model_id"] == "deepseek-v4-flash" and m["price"] == {"input": 0.14, "output": 0.28}
    assert m["context_length"] == 131072  # capped
    # a literal secret is never copied
    # Keyed on the literal the fixture actually carries, not on an "sk-"
    # prefix: the fixture stopped impersonating a real vendor key (those
    # must stay scannable everywhere, tests included) and a prefix check
    # would then have passed no matter what the importer copied.
    assert "a-literal-secret-value-not-an-env-ref" not in json.dumps(cfg)
    assert cfg["providers"]["leaky"]["api_key_env"] == "LEAKY_API_KEY"
    # include_local brings the loopback provider in
    added2 = import_openclaw(cfg, p, include_local=True)
    assert "local" in added2["providers"]


# ----------------------------------------------------------------- GUI

def test_markdown_renders_table_and_escapes():
    from crucibleforge.gui.markdown import md_to_html
    html = md_to_html("# T\n\n| a | b |\n|---|---|\n| 1 | <script> |\n\n**bold** and `x`")
    assert "<table>" in html and "&lt;script&gt;" in html
    assert "<strong>bold</strong>" in html and "<code>x</code>" in html


def test_gui_auth_and_csrf(monkeypatch):
    from crucibleforge.gui import server as srv
    from http.server import BaseHTTPRequestHandler

    class Fake(srv.Handler):
        def __init__(self, headers, path="/api/state"):
            self.headers = headers
            self.path = path
            self.sent = []
        def send_response(self, code): self.sent.append(code)
        def send_header(self, *a): pass
        def end_headers(self): pass
        class _W:
            def write(self, b): pass
        wfile = _W()
        def _read_json(self): return {}
    app = type("A", (), {})()
    app.token = "sekrit"
    Fake.app = app
    # GET without token -> 401
    h = Fake({})
    h.do_GET()
    assert h.sent == [401]
    # POST with token header but cross-site origin -> 403
    h = Fake({"X-CrucibleForge-Token": "sekrit", "Sec-Fetch-Site": "cross-site"}, "/api/stop")
    h.do_POST()
    assert h.sent == [403]
    # wrong token -> 401
    h = Fake({"X-CrucibleForge-Token": "nope"}, "/api/stop")
    h.do_POST()
    assert h.sent == [401]


# ------------------------------------------------------- thinking budget

def test_thinking_budget_auto_detects():
    from crucibleforge.runner import _Ctx
    cfg = {"providers": {"p": {"type": "openai", "base_url": "http://x"}},
           "defaults": {"thinking_max_tokens_factor": 4, "thinking_max_tokens_cap": 10000},
           "judge": {"candidates": []}, "models": []}
    prov = providers.get_provider(cfg, "p")
    ctx = _Ctx(cfg, {"name": "m", "model_id": "m", "provider": "p"}, prov, 8192)
    assert ctx.thinking is None and ctx.budget(1024) == 1024
    ctx._observe(ChatResult(response_text="x", reasoning_text="thinking...", reasoning_tokens=50))
    assert ctx.thinking is True and ctx.budget(1024) == 4096
    assert ctx.budget(4096) == 10000            # capped
    ctx2 = _Ctx(cfg, {"name": "m", "model_id": "m", "provider": "p", "thinking": False}, prov, 8192)
    ctx2._observe(ChatResult(reasoning_text="cot"))
    assert ctx2.thinking is False and ctx2.budget(1024) == 1024


# ------------------------------------------------------ profiles + scoring

def test_standard_profile_loads_and_applies():
    from crucibleforge import profiles
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    prof = profiles.load_profile("standard", cfg)
    cfg2, cases = profiles.apply_profile(prof, cfg)
    assert cfg2["_profile"] == "standard"
    assert cfg2["defaults"]["thinking_max_tokens_cap"] == 16384
    assert cfg2["defaults"]["repeats"]["rp"] == 1
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) == sum(len(v) for v in prof["cases"].values())
    coding = [c for c in cases if c["category"] == "coding"]
    assert coding and all(c["max_tokens"] == 4096 for c in coding)
    assert all(c["difficulty"] == "hard" for c in coding)
    # smoke narrows to smoke-tagged members only
    _, smoke = profiles.apply_profile(prof, cfg, smoke=True)
    assert 0 < len(smoke) < len(cases)
    j = profiles.profile_judge(prof)
    assert j["provider"] and j["model_id"]


def test_profile_unknown_case_rejected(tmp_path):
    from crucibleforge import profiles
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    bad = {"name": "x", "cases": {"coding": ["NOPE-1"]}}
    with pytest.raises(ConfigError):
        profiles.apply_profile(bad, cfg)


def test_scorecard_weights_and_renormalisation():
    from crucibleforge import report
    st = {"rp": {"overall": 8.0}, "nsfw": {"erotic_quality": 7.0, "explicitness_peak": 10, "willingness": 1.0},
          "steer": {"rate": 1.0}, "coding": {"rate": 0.2}, "tooluse": {"rate": 0.9},
          "instruct": {"rate": 0.5}, "reasoning": {"rate": None}, "speed": {"tok_per_s_median": 70}}
    c = report.scorecard(st, report.scoring_config(None))
    # chat = (80*20 + 70*20 + 100*5 + 100*5 + 100*5)/55
    assert c["chat"] == pytest.approx((80*20 + 70*20 + 100*15) / 55)
    # code: reasoning missing -> weights 20+10+10 = 40
    assert c["code"] == pytest.approx((20*20 + 90*10 + 50*10) / 40)
    assert c["missing"] == ["reasoning"]
    assert c["ts"] == 70 and c["tok_per_s"] == 70
    # total combines halves by their present weights (55 + 40)
    assert c["total"] == pytest.approx((c["chat"] * 55 + c["code"] * 40) / 95)
    # user override
    sc = report.scoring_config({"scoring": {"tok_per_s_full_marks": 35, "code": {"coding": 100}}})
    c2 = report.scorecard(st, sc)
    assert c2["ts"] == 100 and c2["code"] == pytest.approx(20)


def test_select_judge_accepts_dict_override():
    cfg = {"providers": {"p": {"type": "openai", "base_url": "http://x"}},
           "defaults": {}, "models": [], "judge": {"candidates": []}}
    j = judge.select_judge(cfg, set(), override={"provider": "p", "model_id": "m", "extra_body": {"a": 1}})
    assert j["extra_body"] == {"a": 1}
    with pytest.raises(judge.JudgeError):
        judge.select_judge(cfg, {"m"}, override={"provider": "p", "model_id": "m"})


# ------------------------------------------------- studioforge recommended load

def test_recommended_load_picks_best_fitting_profile(monkeypatch):
    from crucibleforge import studioforge
    import httpx

    class FakeResp:
        status_code = 200
        content = b"x"
        headers = {}
        def __init__(self, d): self._d = d
        def json(self): return self._d
    profiles = {"profiles": [
        {"mode": "slow", "fits": True, "est_gen_tps": 30, "max_parallel": 8,
         "load_args": {"model_id": "m", "ctx_size": 32768, "parallel": 8, "kv_cache_type": "f16"}},
        {"mode": "fast", "fits": True, "est_gen_tps": 60, "max_parallel": 5,
         "load_args": {"model_id": "m", "ctx_size": 32768, "parallel": 5, "kv_cache_type": "f16"}},
        {"mode": "nofit", "fits": False, "est_gen_tps": 99, "max_parallel": 9,
         "load_args": {"model_id": "m", "ctx_size": 32768, "parallel": 9}}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, headers=None): return FakeResp(profiles)
        def request(self, method, url, json=None, headers=None): return FakeResp(profiles)
    monkeypatch.setattr(httpx, "Client", FakeClient)
    args = studioforge.recommended_load("m", "http://x/v1", "", context_length=16384)
    assert args["parallel"] == 5 and args["_profile"]["mode"] == "fast"
    assert args["ctx_size"] == 16384          # registry ctx caps the recommendation
    assert "model_id" not in args


def test_studioforge_workers_follow_server(monkeypatch):
    from crucibleforge import studioforge
    cfg = {"providers": {"sf": {"type": "studioforge", "base_url": "http://x/v1"},
                         "sf2": {"type": "studioforge", "base_url": "http://y/v1", "concurrency": 2}},
           "defaults": {}, "judge": {"candidates": []}, "models": []}
    monkeypatch.setattr(studioforge, "loaded_parallel", lambda *a: 5)
    assert providers.get_provider(cfg, "sf").workers("m") == 5       # auto = follow server
    assert providers.get_provider(cfg, "sf2").workers("m") == 2      # explicit cap wins


def test_load_recommended_handles_507_and_404(monkeypatch):
    from crucibleforge import studioforge
    import httpx

    class R:
        headers = {}
        def __init__(self, code, d): self.status_code, self._d, self.content, self.text = code, d, b"x", "x"
        def json(self): return self._d

    def fake_client(code, d):
        class C:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def request(self, method, url, json=None, headers=None):
                assert method == "POST"
                assert url.endswith("/api/models/m/load-recommended") and json["ctx_size"] == 16384
                return R(code, d)
        return C
    monkeypatch.setattr(httpx, "Client", fake_client(200, {"plan": {"parallel": 5}}))
    assert studioforge.load_recommended("m", "http://x/v1", "", 16384)["plan"]["parallel"] == 5
    monkeypatch.setattr(httpx, "Client", fake_client(507, {"detail": "does not fit"}))
    r = studioforge.load_recommended("m", "http://x/v1", "", 16384)
    assert r["_status"] == 507 and "fit" in r["detail"]
    monkeypatch.setattr(httpx, "Client", fake_client(404, {}))
    assert studioforge.load_recommended("m", "http://x/v1", "", 16384) is None
