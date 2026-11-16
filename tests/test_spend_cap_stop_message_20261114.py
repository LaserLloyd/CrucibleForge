"""A run stopped by its spend cap says so (lead 2026-11-14, D's note): "stopped: spend cap reached
($X of the $Y cap)", not "stopped by user". No row is written and the job stays pending, as for any
stop; a cap stop on a rung does not count against the rung."""
from __future__ import annotations

import json

from crucibleforge import config, providers, runner, spend, stopflag
from test_runner_stop_39 import _cfg as stop_cfg, _cases


def test_the_stop_line_names_both_figures():
    e = spend.SpendCapExceeded("x", spent=7.3912, cap=8.0)
    assert runner.cap_stop_reason(e) == "stopped: spend cap reached ($7.39 of the $8.00 cap)"
    g = spend.SpendCapExceeded("x", spent=331.2, cap=330.0, cap_name="global cap")
    assert runner.cap_stop_reason(g) == "stopped: spend cap reached ($331.20 of the $330.00 global cap)"
    gate = spend.SpendCapExceeded("hosted model 'm' has no price entry")
    assert runner.cap_stop_reason(gate) == "stopped: spend cap reached (hosted model 'm' has no price entry)"


def test_the_cap_refusals_carry_their_figures(monkeypatch):
    monkeypatch.setattr(spend, "_journal", lambda rec: None)
    e = spend._over(8.0, "grok-x", 7.3912, 0.9)
    assert (e.spent, e.cap, e.cap_name) == (7.3912, 8.0, "cap") and "spend cap reached" in str(e)


def test_a_run_over_its_cap_stops_with_the_cap_line(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)

    def chat(self, model_id, messages, **kw):
        raise spend.SpendCapExceeded("crucibleforge spend cap reached: $7.3900 spent or reserved of the "
                                     "$8.00 cap", spent=7.39, cap=8.0)
    monkeypatch.setattr(providers.Provider, "chat", chat)
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"m"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": True}
    stopflag.ABORT.clear()
    runner.WINDOW_STOP.clear()
    runner.STOP.clear()
    summary = runner.run_models(stop_cfg(), [entry], _cases())
    assert summary["m"]["error"] == "stopped: spend cap reached ($7.39 of the $8.00 cap)"
    assert summary["m"]["stopped"] and summary["m"]["spend_cap"]
    assert config.load_transcripts("m") == []                         # no row: the job stays pending
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["error"] == summary["m"]["error"] and not meta.get("stopped")


def test_a_cap_stop_is_not_the_rungs_fault(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    recs = [{"failed": True, "rung": 2, "ts": "2026-11-14T10:00:00",
             "error": "stopped: spend cap reached ($7.39 of the $8.00 cap)"}] * 5
    (tmp_path / "meta_m.json").write_text(json.dumps({"escalated": recs}), encoding="utf-8")
    assert runner.blocked_rungs("m") == set()
    recs[0] = {**recs[0], "error": "rung 2 failed: server error"}
    (tmp_path / "meta_m.json").write_text(json.dumps({"escalated": [recs[0]] * 5}), encoding="utf-8")
    assert runner.blocked_rungs("m") == {2}                              # a real failure still counts
