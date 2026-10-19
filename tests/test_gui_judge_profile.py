"""The GUI's "Judge pending rows" uses the benchmark's (profile) judge.

`crucibleforge judge` takes the judge the bench profile names; the GUI's
/api/judge used to pass no judge at all, so run_judge walked
judge.candidates and scored rows with a model whose verdicts do not count on
the board (`profiles.default_judge_id`)."""
from __future__ import annotations

from crucibleforge import config, profiles
from crucibleforge.gui import server as srv


class _Fake(srv.Handler):
    def __init__(self):
        self.headers = {}
        self.path = "/api/judge"
        self.sent = []
        self.body = None

    def _json(self, obj, code=200, extra=None):
        self.sent.append(code)
        self.body = obj


def _drive(monkeypatch, data):
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    app = type("A", (), {})()
    app.reload = lambda: cfg
    _Fake.app = app
    started = {}
    monkeypatch.setattr(srv.JOB, "start", lambda kind, fn: started.setdefault("fn", fn))
    monkeypatch.setattr(srv.JOB, "status", lambda: {})
    seen = {}

    def fake_run_judge(cfg2, labels, **kw):
        seen.update(kw, cfg=cfg2, labels=labels)
        return {}

    import crucibleforge.judge as judge_mod
    import crucibleforge.report as report_mod
    monkeypatch.setattr(judge_mod, "run_judge", fake_run_judge)
    monkeypatch.setattr(report_mod, "generate", lambda *_a, **_k: "")
    h = _Fake()
    h._api_post("/api/judge", data)
    assert h.sent == [200]
    started["fn"]()
    return cfg, seen


def test_gui_judge_defaults_to_the_profile_judge(monkeypatch):
    cfg, seen = _drive(monkeypatch, {"models": [config.load_config(
        config.EXAMPLE_CONFIG_PATH)["models"][0]["name"]]})
    want = profiles.profile_judge(profiles.load_profile(profiles.DEFAULT_PROFILE, cfg))
    assert want, "the bench profile names a judge"
    assert seen["judge_override"] == want
    # judged under the profile's budgets, like the CLI
    assert seen["cfg"].get("_profile") == profiles.DEFAULT_PROFILE


def test_gui_judge_explicit_pick_wins(monkeypatch):
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    _, seen = _drive(monkeypatch, {"models": [cfg["models"][0]["name"]], "judge": "some-label"})
    assert seen["judge_override"] == "some-label"


def test_gui_state_names_the_bench_judge(monkeypatch):
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    want = profiles.profile_judge(profiles.load_profile(profiles.DEFAULT_PROFILE, cfg))
    assert srv._bench_judge(cfg)[1] == want
