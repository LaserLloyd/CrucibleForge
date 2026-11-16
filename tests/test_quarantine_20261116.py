"""R1 quarantine records, R4 batch-continues and the circuit breaker, R5 digest."""
import json

import yaml

from crucibleforge import cli, conformance, quarantine


def _cfg():
    return {
        "providers": {
            "rig": {"type": "studioforge", "base_url": "http://rig.invalid/v1"},
            "api": {"type": "openai", "base_url": "https://example.invalid/v1",
                    "api_key_env": "UNUSED_KEY"},
        },
        "models": [
            {"name": "bare-a", "provider": "rig", "model_id": "org/a", "context_length": 8192},
            {"name": "bare-b", "provider": "rig", "model_id": "org/b", "context_length": 8192},
            {"name": "bare-c", "provider": "rig", "model_id": "org/c", "context_length": 8192},
            {"name": "hosted", "provider": "api", "model_id": "hosted-1", "context_length": 8192,
             "price": {"input": 1, "output": 1}},
        ],
        "judge": {"candidates": []},
        "defaults": {"regime": "deployed"},
    }


def test_hold_does_not_expire_and_does_not_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", str(tmp_path))
    a = quarantine.hold_settings("m", "no card")
    b = quarantine.hold_settings("m", "no card again")
    assert a["id"] == b["id"] and b["_new"] is False
    text = (tmp_path / f"{a['id']}.yaml").read_text(encoding="utf-8")
    assert "expires" not in text and "status: open" in text
    old = {"id": "q-20200101-001", "model": "old-model", "stage": "preflight",
           "class": "needs_settings", "scope": "model", "status": "open", "opened": "2020-01-01T00:00:00Z"}
    (tmp_path / "q-20200101-001.yaml").write_text(
        yaml.safe_dump(old, sort_keys=False), encoding="utf-8")
    ids = [r["id"] for r in quarantine.records() if r.get("status") == "open"]
    assert "q-20200101-001" in ids


def test_three_consecutive_same_class_halts_the_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", str(tmp_path))
    for name in ("a", "b", "c"):
        quarantine.hold_settings(name, "unchecked")
    halt = quarantine.queue_halted()
    assert halt and halt["class"] == "circuit"
    assert "auth" not in (tmp_path / "alerts.jsonl").read_text(encoding="utf-8")
    assert "halt" in (tmp_path / "alerts.jsonl").read_text(encoding="utf-8")
    # a fourth of the same class does not open a second halt
    quarantine.hold_settings("d", "unchecked")
    assert len(list(tmp_path.glob("q-*.yaml"))) == 5  # 4 models + 1 halt


def test_a_different_class_does_not_trip_the_breaker(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", str(tmp_path))
    quarantine.hold_settings("a", "unchecked")
    quarantine.hold_settings("b", "unchecked")
    quarantine.hold(model="c", stage="run", klass="degenerate_output", reason="loop")
    assert quarantine.queue_halted() is None


def test_closing_the_halt_lets_the_queue_run(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", str(tmp_path))
    for name in ("a", "b", "c"):
        quarantine.hold_settings(name, "unchecked")
    halt = quarantine.queue_halted()
    assert halt
    quarantine.set_status(halt["id"], "escalated")
    assert quarantine.queue_halted() is None


def test_auth_alerts_once(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", str(tmp_path))
    quarantine.note_auth("deepseek", "HTTP 401")
    quarantine.note_auth("deepseek", "HTTP 401")
    lines = (tmp_path / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["kind"] == "auth"


def test_digest_is_ten_lines_and_publish_stays_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", str(tmp_path))
    quarantine.hold_settings("a", "unchecked")
    quarantine.note_auth("deepseek", "HTTP 401")
    lines = quarantine.digest_lines(today="2026-11-16")
    assert len(lines) <= 10
    assert lines[1] == "published: off (human tap)"
    assert "needs_settings=1" in lines[2]
    assert "deepseek" in lines[6]


def test_conform_continues_past_a_blocked_model(tmp_path, monkeypatch, capsys):
    """One blocked rig model does not fail the batch when another model runs."""
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", str(tmp_path / "q"))
    cfg = _cfg()
    # hosted models are exempt from the settings gate; keep only one rig + the hosted row
    cfg["models"] = [m for m in cfg["models"] if m["name"] in ("bare-a", "hosted")]

    class _Rec:
        label = "hosted"
        model_id = "hosted-1"
        provider = "api"
        provider_type = "openai"
        verdict = conformance.PASS
        checks = []
        note = ""

        def to_dict(self):
            return {"model_label": self.label, "verdict": self.verdict, "checks": []}

    monkeypatch.setattr(conformance, "conform", lambda *a, **k: [_Rec()])
    monkeypatch.setattr(conformance, "with_profile", lambda c, p=None: c)
    monkeypatch.setattr(cli, "rig_phase", lambda **k: _Null())
    monkeypatch.setattr("crucibleforge.settings_gate.rig_templates", lambda *a, **k: {})
    rc = cli.cmd_conform(type("A", (), {"models": "all", "json": False,
                                        "allow_load": False, "profile": None})(), cfg)
    out = capsys.readouterr().out
    assert rc == 0
    assert "needs_settings" in out and "hosted" in out
    assert any(p.suffix == ".yaml" for p in (tmp_path / "q").glob("q-*.yaml"))


def test_conform_fails_when_every_model_is_blocked(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", str(tmp_path / "q"))
    cfg = _cfg()
    cfg["models"] = [m for m in cfg["models"] if m["name"] == "bare-a"]
    monkeypatch.setattr(conformance, "conform", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    monkeypatch.setattr(conformance, "with_profile", lambda c, p=None: c)
    monkeypatch.setattr(cli, "rig_phase", lambda **k: _Null())
    monkeypatch.setattr("crucibleforge.settings_gate.rig_templates", lambda *a, **k: {})
    rc = cli.cmd_conform(type("A", (), {"models": "all", "json": False,
                                        "allow_load": False, "profile": None})(), cfg)
    assert rc == 1
    assert "needs_settings" in capsys.readouterr().out


def test_early_stop_record_keeps_the_signal_not_the_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", str(tmp_path))
    rec = quarantine.hold_early_stop(
        "nanbeige", {"signal": "misrouted", "value": 0.55, "threshold": 0.25, "after_answers": 20},
        transcript="/results/transcripts_nanbeige.jsonl")
    body = yaml.safe_load((tmp_path / f"{rec['id']}.yaml").read_text(encoding="utf-8"))
    assert body["class"] == "template_trap"
    assert body["tripwire"]["value"] == 0.55
    assert body["evidence"]["outputs"].endswith(".jsonl")
    assert "response" not in body["evidence"]


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
