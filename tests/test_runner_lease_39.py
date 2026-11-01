"""Suite 3.9 runner: GPU lease hygiene (EVAL-FABLE M9, STEP4 defect 2 + the
orphaned lease).

2026-10-28: with the placement check unavailable the judge leased the whole
set [0,1,2,3] — GPU 3 was SpeechForge's lease. A crashed (SIGABRTed) run left
lease 92fa55704e1e behind; the next run would have stacked a second lease.
"""
from __future__ import annotations


import pytest

from crucibleforge import judge, providers, studioforge


def _sf_cfg(**prov):
    return {"providers": {"sf": {"type": "studioforge", "base_url": "http://sf.invalid/v1",
                                 "lease": True, **prov}},
            "defaults": {}, "models": [], "judge": {"candidates": []}}


CAND = {"provider": "sf", "model_id": "j/judge-31b-GGUF/q8"}
SPEECH = {"id": "S1", "holder": "speechforge", "holder_family": "speechforge",
          "kind": "tenant", "devices": [3], "model_ids": []}


@pytest.fixture
def lease_calls(monkeypatch):
    calls = {"acquire": [], "release": []}

    def acquire(base_url, api_key, headers, devices, **kw):
        calls["acquire"].append(list(devices))
        return {"_lease_id": "NEW", "id": "NEW"}

    monkeypatch.setattr(studioforge, "acquire_lease", acquire)
    monkeypatch.setattr(studioforge, "release_lease",
                        lambda b, k, h, lid: calls["release"].append(lid) or True)
    monkeypatch.setattr(studioforge, "vacate_render_leases", lambda *a, **k: False)
    monkeypatch.setattr(judge, "select_judge", lambda cfg, ex, override=None: dict(CAND))
    providers.clear_cache()
    yield calls
    providers.clear_cache()


# -------------------------------------------------------- device choice (M9)

def test_unknown_placement_keeps_the_preferred_cards_never_every_card(monkeypatch):
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: (500, {}))
    assert studioforge.choose_lease_devices("u", "", None, "m", [0, 1], [0, 1, 2, 3]) == [0, 1]


def test_cards_another_holder_leases_are_never_requested(monkeypatch):
    # planner: does not fit on the 5090s -> the rest of the set, minus GPU 3
    monkeypatch.setattr(studioforge, "placement_fits", lambda *a, **k: False)
    got = studioforge.choose_lease_devices("u", "", None, "m", [0, 1], [0, 1, 2, 3], busy={3})
    assert got == [0, 1, 2]
    # nothing free at all: a clear refusal, not a lease on someone's card
    with pytest.raises(studioforge.StudioForgeError, match="leased by another holder"):
        studioforge.choose_lease_devices("u", "", None, "m", None, [3], busy={3})


def test_foreign_held_devices_skip_our_own_and_vacatable_render_leases():
    leases = [SPEECH,
              {"id": "R", "holder": "clawforge2", "holder_family": "clawforge", "kind": "render",
               "devices": [2]},
              {"id": "J", "holder": "crucibleforge-judge", "devices": [0, 1]}]
    assert studioforge.foreign_held_devices(leases) == {2, 3}
    assert studioforge.foreign_held_devices(leases, vacatable_render=True) == {3}


def test_judge_lease_never_includes_speechforges_gpu(monkeypatch, lease_calls):
    """The STEP4 scene: configured set [0,1,2,3], preferred [0,1], SpeechForge
    on GPU 3. Placement unknown -> [0,1]; does not fit -> [0,1,2]; never 3."""
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: [SPEECH])
    cfg = _sf_cfg(lease_devices=[0, 1, 2, 3], lease_devices_preferred=[0, 1])
    monkeypatch.setattr(studioforge, "placement_fits", lambda *a, **k: None)
    judge.acquire_judge_lease(cfg)
    providers.clear_cache()
    monkeypatch.setattr(studioforge, "placement_fits", lambda *a, **k: False)
    judge.acquire_judge_lease(cfg)
    assert lease_calls["acquire"] == [[0, 1], [0, 1, 2]]


def test_judge_lease_without_a_device_config_uses_the_free_rig_cards(monkeypatch, lease_calls):
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: [SPEECH])
    monkeypatch.setattr(studioforge, "gpu_indices", lambda *a, **k: [0, 1, 2, 3])
    judge.acquire_judge_lease(_sf_cfg())
    assert lease_calls["acquire"] == [[0, 1, 2]]


def test_a_refusal_while_choosing_cards_is_reported_not_an_unbound_local(monkeypatch, lease_calls):
    """EVAL M9: ``devices`` was first bound inside the try, so a refusal from
    the card choice raised UnboundLocalError in the except path."""
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: [])

    def boom(self, model_id, exclude=()):
        raise studioforge.StudioForgeError("render lease(s) still stand", status=409)

    monkeypatch.setattr(providers.Provider, "claim_devices", boom)
    with pytest.raises(judge.JudgeLeaseUnavailable, match="still stand"):
        judge.acquire_judge_lease(_sf_cfg(lease_devices_preferred=[0, 1]))


# ---------------------------------------------------- orphaned lease (STEP4)

def _old(lid, model="m"):
    return {"id": lid, "holder": "crucibleforge", "devices": [0, 1], "model_ids": [model],
            "reason": f"crucibleforge benchmark: {model}"}


def test_a_crashed_runs_lease_is_released_before_a_new_one(monkeypatch, lease_calls):
    leases = [
        _old("ORPHAN"),                                  # left by a run that died
        _old("OTHERMODEL", model="z"),                   # another model: untouched
        {**_old("JUDGE"), "holder": "crucibleforge-judge"},   # another holder
        SPEECH,
    ]
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: leases)
    monkeypatch.setattr(studioforge, "wait_ready", lambda *a, **k: {})
    monkeypatch.setattr(studioforge, "load_model", lambda *a, **k: 0.0)
    monkeypatch.setattr(studioforge, "loaded_plan", lambda *a, **k: None)
    p = providers.get_provider(_sf_cfg(lease_devices=[0, 1]), "sf")
    p.use_resident = False
    p.switch_model("m", 40960)
    assert lease_calls["release"] == ["ORPHAN"]
    assert lease_calls["acquire"] == [[0, 1]]


def test_a_stale_judge_lease_is_released_and_the_own_lease_kept(monkeypatch, lease_calls):
    leases = [{**_old("OLDJ", model=CAND["model_id"]), "holder": "crucibleforge-judge"},
              {**_old("OWN"), "holder": "crucibleforge"}]
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: leases)
    judge.acquire_judge_lease(_sf_cfg(lease_devices=[0, 1]))
    assert lease_calls["release"] == ["OLDJ"]
    assert studioforge.stale_leases(leases[1:], "crucibleforge", "m", own_lease_id="OWN") == []
