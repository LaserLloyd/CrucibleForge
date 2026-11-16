"""The ladder judge pass end to end on a copy of a real pilot folder, with a fake judge (no rig): the rung check runs
and is cached per ladder, the board-current rows are placed and appended with ``judge_ladder``, Grok (rung R1)
meets its own rung as its other seed, a second pass asks nothing, and the rc4-main-ladder board reads the placements
(no unit pending). Box-only: skipped without /var/tmp/mb45-build/d2-work."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from crucibleforge import config, profiles, report
from crucibleforge import rpladder as RL
from crucibleforge import rpladder_run as LR

D2 = Path("/var/tmp/mb45-build/d2-work")
pytestmark = pytest.mark.skipif(not (D2 / "results-pilot-grok").exists(), reason="box-only: pilot folders")
LABEL = "grok-4-7"


class FakeJC:
    model_id = None
    thinking = True
    extra_body = None
    concurrency = 4

    def __init__(self, mid):
        self.model_id = mid
        self.calls = 0

    def chat(self, messages, **kw):
        assert kw["response_format"] == RL.SCHEMA and kw["max_tokens"] == 300 and kw["temperature"] == 0
        assert kw["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
        self.calls += 1
        body = messages[1]["content"]
        a = body.split("## Text A")[1].split("## Text B")[0]
        b = body.split("## Text B")[1].split("JSON only")[0]
        v = "none" if abs(len(a) - len(b)) < 0.1 * max(len(a), len(b)) else ("A" if len(a) > len(b) else "B")
        return SimpleNamespace(response_text=json.dumps({"better": v, "reason": "len"}))


def test_the_pass_places_appends_and_the_board_reads_it(tmp_path, monkeypatch):
    res = tmp_path / "results"
    res.mkdir()
    for f in (D2 / "results-pilot-grok").glob("transcripts_*.jsonl"):
        shutil.copy(f, res / f.name)
    monkeypatch.setattr(config, "_RESULTS_DIR", res, raising=False)
    config.set_results_dir(res)
    monkeypatch.setenv("CRUCIBLEFORGE_RESULTS", str(res))
    monkeypatch.setenv("CF_COMPOSITION", "rc4-main-ladder")
    profiles.set_active("bench45")
    prof = profiles.load_profile("bench45")
    profiles.activate_cases_root(prof)
    # the live folder's session rows were graded under an older session bank: regrade the COPY to today's bank
    # first (the restamp step of the clean pass; replies untouched), else no row is board-current
    from crucibleforge import chains, regrade
    from crucibleforge.gen import _det
    tf = next(res.glob("transcripts_*.jsonl"))
    rows = [json.loads(x) for x in tf.read_text(encoding="utf-8").splitlines() if x.strip()]
    specs = {c["id"]: c for c in chains.chain_cases(prof)}
    recs = regrade.plan(rows, specs, "test: today's session bank", None, {"empty_stop_rule": False},
                        "chat/j_rpsession", now="2026-11-15T10:30:00+00:00", commit="test", pin=_det.pinned_python(prof))
    new = [r for rec in recs if rec["status"] == "regrade" for r in rec["rows"]]
    with tf.open("a", encoding="utf-8") as fh:
        for r in new:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    jm = prof["judge"]["model_id"]
    jc = FakeJC(jm)
    from crucibleforge import judge as J
    monkeypatch.setattr(J, "_load_judge", lambda *a, **k: jc)
    cfg = {"_profile": "bench45", "models": [{"name": LABEL, "model_id": "grok-4.7"}]}
    out = LR.run(cfg, prof, [LABEL], windows=[1, 3])
    assert out["rung_check_complete"] and len(out["rung_check"]) == 24
    assert len(list((res / "rpladder" / "rungcheck").glob("*.json"))) == 6
    d = out["labels"][LABEL]
    assert d["placed"] == 12 and d["pending"] == 0
    first = jc.calls
    assert first > 0
    rows = config.load_transcripts(LABEL)
    recs = [r["judge_ladder"] for r in rows if isinstance(r.get("judge_ladder"), dict)]
    assert len(recs) == 12
    cards = RL.load()
    units, other = LR._units(LR.current_session_rows(LABEL, cfg), cards)
    by_unit = {u["unit"]: u for u in units}
    n_r1 = 0
    for rec in recs:
        assert RL.stale_reason(rec, cards, None, jm) is None
        if not rec.get("no_ladder"):
            u = by_unit[rec["unit"]]
            own, oth = RL.anchor_sha(u["replies"]), RL.anchor_sha(other(u)["replies"])
            assert own != oth
            for p in (p for p in rec["pairs"] if p["rung"] == 1):   # Grok is R1: it meets its OTHER seed there
                assert p["anchor_sha"] == oth and p["anchor_sha"] != own
                n_r1 += 1
    assert n_r1 > 0
    # a second pass: nothing to place, nothing asked
    out2 = LR.run(cfg, prof, [LABEL], windows=[1, 3])
    assert jc.calls == first and out2["labels"][LABEL]["placed"] == 0
    # the board reads every placement
    brows = report.board_filter(LABEL, cfg, rows=rows)
    s = report.stats_from_tier_rows(LABEL, cfg, prof, {1: brows}, {}, None)
    cs = s["chain_stats"]["chat/j_rpsession"]
    assert cs["pending_judge"] == 0 and cs["complete"]


def test_attach_uses_the_resident_copy_and_never_loads(tmp_path, monkeypatch):
    """--attach (rig operator owns the load): no _load_judge, no lease, no unload; refuses when not ready."""
    from crucibleforge import judge as J, studioforge, providers
    cfg = {"providers": {"studioforge": {"type": "studioforge", "base_url": "http://x/v1", "api_key": "k",
                                         "lease": True, "use_resident": False}},
           "judge": {"candidates": [{"name": "j", "provider": "studioforge", "model_id": "judge-m",
                                     "context_length": 16384, "thinking": True, "instance_name": "mblab-e",
                                     "instance_devices": [0, 1], "instance_parallel": [4, 3]}]},
           "models": []}
    monkeypatch.setattr(J, "_load_judge", lambda *a, **k: (_ for _ in ()).throw(AssertionError("loaded")))
    for bad in ("_lease_load", "switch_model", "release_lease"):
        monkeypatch.setattr(providers.Provider, bad, lambda *a, **k: (_ for _ in ()).throw(AssertionError(bad)),
                            raising=False)
    monkeypatch.setattr(J, "select_judge", lambda cfg, b, override=None, instance=None: cfg["judge"]["candidates"][0])
    monkeypatch.setattr(studioforge, "loaded_plan", lambda *a, **k: {"state": "loading"})
    with pytest.raises(RuntimeError, match="not resident and ready"):
        LR.attach_judge(cfg, set())
    seen = {}
    monkeypatch.setattr(studioforge, "loaded_plan",
                        lambda mid, *a, **k: seen.setdefault("mid", mid) and {"state": "ready", "parallel": 4,
                                                                              "ctx_size": 16384, "devices": [0, 1]})
    jc = LR.attach_judge(cfg, set())
    assert seen["mid"] == "judge-m" and jc.concurrency == 4 and jc.model_id == "judge-m"
    sent = {}
    monkeypatch.setattr(providers.Provider, "chat", lambda self, mid, msgs, client_name=None, **kw:
                        sent.update(client=client_name, mid=mid), raising=False)
    jc = LR.attach_judge(cfg, set(), client="mblab-rigtput-e")
    jc.chat([{"role": "user", "content": "x"}])
    assert sent == {"client": "mblab-rigtput-e", "mid": "judge-m"}
