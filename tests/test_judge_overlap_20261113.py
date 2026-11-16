"""The judge beside generation (judge_overlap): the schedule follows the placement, the
judge copy is loaded exactly on its cards, judges in passes, and is released on every path."""
from __future__ import annotations

import threading

import pytest

from crucibleforge import judge, judge_overlap, providers, stopflag, studioforge
from crucibleforge import subject_copies as sc

JID = "org/judge-31B-Q8_0"
SJID = JID + "@b"


@pytest.fixture(autouse=True)
def _flags():
    stopflag.ABORT.clear()
    providers.clear_cache()
    yield
    providers.clear_cache()


def _cfg(copies):
    return {"defaults": {}, "providers": {"p": {"type": "studioforge",
                                                "base_url": "http://p.invalid/v1"}},
            "judge": {"candidates": []},
            "models": [{"name": "ref", "model_id": "org/ref", "provider": "p"},
                       {"name": "api", "model_id": "x/api", "provider": "h"}],
            "subject_placement": {"kv_cache_type": "q8_0", "copies": copies,
                                  "judge": {"model_id": JID, "name": "j", "devices": [2, 3],
                                            "parallel": [4, 3]}}}


ONE = [{"name": "a", "devices": [0, 1], "slots": [10, 8]}]
TWO = ONE + [{"name": "b", "devices": [2, 3], "slots": [4]}]


def test_mode_follows_the_placement():
    mode, inst = judge_overlap.mode_for_run(_cfg(ONE), ["ref"])
    assert mode == "concurrent" and inst["instance_devices"] == [2, 3]
    assert judge_overlap.mode_for_run(_cfg(TWO), ["ref"])[0] == "after"
    cfg = _cfg(ONE)
    cfg["subject_placement"] = {}
    assert judge_overlap.mode_for_run(cfg, ["ref"])[0] is None


class _Rig:
    log: list = []
    land_on = None

    def __init__(self, provider, holder_base=None):
        self.p = provider
        self.base, self.key = "http://p.invalid", ""

    def hdrs(self, holder=None):
        return {}

    def status_entry(self, sid):
        return None

    def base_fingerprint(self, mid):
        return {"instance": None, "leases_naming_base": []}

    def lease(self, c, holder):
        _Rig.log.append(("lease", holder, list(c.devices)))
        return "LJ"

    def load(self, c, ctx):
        _Rig.log.append(("load", c.serve_id, list(c.devices), c.slots[0], c.kv, ctx))
        return {"state": "ready", "parallel": c.slots[0], "ctx_size": ctx,
                "devices": _Rig.land_on or list(c.devices), "kv_cache_type": c.kv}

    def holder(self, name):
        return f"crucibleforge-judge-{name}"

    def unload(self, sid, name=None):
        _Rig.log.append(("unload", sid))


def _patch(monkeypatch, passes):
    _Rig.log, _Rig.land_on = [], None
    monkeypatch.setattr(sc, "Rig", _Rig)
    monkeypatch.setattr(judge_overlap, "PASS_GAP_S", 0.01)
    monkeypatch.setattr(judge, "select_judge",
                        lambda cfg, ids, **k: {"model_id": JID, "provider": "p",
                                               "serve_model_id": SJID,
                                               "context_length": 16384})
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: [])
    released = []
    monkeypatch.setattr(providers.Provider, "release_lease",
                        lambda self: released.append(self._lease and self._lease["_lease_id"]))
    monkeypatch.setattr(providers.Provider, "_start_keepalive", lambda self: None)
    monkeypatch.setattr(studioforge, "unload_models",
                        lambda b, k, h, ids, timeout_s=0: _Rig.log.append(("unload", *ids)))

    def run_judge(cfg, labels, **kw):
        assert "instance_parallel" not in kw["instance"]       # never reloads the copy
        passes.append(list(labels))
        return {"judged": 2}
    monkeypatch.setattr(judge, "run_judge", run_judge)
    return released


def test_overlap_judges_in_passes_and_releases(monkeypatch):
    passes: list = []
    released = _patch(monkeypatch, passes)
    mode, inst = judge_overlap.mode_for_run(_cfg(ONE), ["ref"])
    oj = judge_overlap.OverlapJudge(_cfg(ONE), ["ref"], inst).start()
    while len(passes) < 3:
        threading.Event().wait(0.01)
    r = oj.finish(timeout=10)
    assert r["loaded"] and not r["error"] and r["passes"] >= 4      # + the last pass
    assert ("lease", "crucibleforge-judge-j", [2, 3]) in _Rig.log
    assert ("load", SJID, [2, 3], 4, "q8_0", 16384) in _Rig.log     # exact, with KV
    assert ("unload", SJID) in _Rig.log and released == ["LJ"]


def test_a_misplaced_judge_is_unloaded_and_never_judges(monkeypatch):
    passes: list = []
    released = _patch(monkeypatch, passes)
    _Rig.land_on = [0, 1]
    _, inst = judge_overlap.mode_for_run(_cfg(ONE), ["ref"])
    r = judge_overlap.OverlapJudge(_cfg(ONE), ["ref"], inst).start().finish(timeout=10)
    assert "outside its lease" in r["error"] and not passes
    assert ("unload", SJID) in _Rig.log and released == ["LJ"]


def test_the_overlap_uses_its_own_provider_object():
    cfg = _cfg(ONE)
    _, inst = judge_overlap.mode_for_run(cfg, ["ref"])
    oj = judge_overlap.OverlapJudge(cfg, ["ref"], inst)
    assert providers.get_provider(oj.cfg, "p") is not providers.get_provider(cfg, "p")


def test_an_overlap_judge_without_its_own_virtual_id_never_leases(monkeypatch):
    """02:10 incident rule: the judge is served only under a distinct virtual id."""
    passes: list = []
    _patch(monkeypatch, passes)
    monkeypatch.setattr(judge, "select_judge",
                        lambda cfg, ids, **k: {"model_id": JID, "provider": "p",
                                               "context_length": 16384})
    _, inst = judge_overlap.mode_for_run(_cfg(ONE), ["ref"])
    r = judge_overlap.OverlapJudge(_cfg(ONE), ["ref"], inst).start().finish(timeout=10)
    assert "own virtual id" in (r["error"] or "") and not passes
    assert not [e for e in _Rig.log if e[0] in ("lease", "load", "unload")]


def test_an_overlap_judge_whose_id_another_lease_names_never_leases(monkeypatch):
    passes: list = []
    _patch(monkeypatch, passes)
    monkeypatch.setattr(studioforge, "list_leases",
                        lambda *a, **k: [{"holder": "crucibleforge-judge-mblab-d",
                                          "devices": [2, 3], "model_ids": [SJID]}])
    _, inst = judge_overlap.mode_for_run(_cfg(ONE), ["ref"])
    r = judge_overlap.OverlapJudge(_cfg(ONE), ["ref"], inst).start().finish(timeout=10)
    assert "named by lease" in (r["error"] or "") and not passes
    assert not [e for e in _Rig.log if e[0] in ("lease", "load", "unload")]
