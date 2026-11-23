"""The judge lease is released under the identity that took it.

2026-11-19 reference run: a run with a labelled holder ($CF_LEASE_HOLDER) took the
judge lease as "crucibleforge-judge" but released it as the run's own holder; the
rig refused (HTTP 403, another holder), the lease stayed, and every ladder round
after the judge found its cards "leased by another holder".
"""
from __future__ import annotations

import pytest

from crucibleforge import judge, providers, studioforge

CAND = {"provider": "sf", "model_id": "j/judge-31b-GGUF/q8"}


def _cfg():
    return {"providers": {"sf": {"type": "studioforge", "base_url": "http://sf.invalid/v1",
                                 "lease": True, "lease_devices": [0, 1]}},
            "defaults": {}, "models": [], "judge": {"candidates": []}}


@pytest.fixture
def calls(monkeypatch):
    out = {"acquire_client": [], "release_client": []}

    def acquire(base_url, api_key, headers, devices, **kw):
        out["acquire_client"].append((headers or {}).get("X-SF-Client"))
        return {"_lease_id": "L1", "id": "L1"}

    monkeypatch.setattr(studioforge, "acquire_lease", acquire)
    monkeypatch.setattr(studioforge, "release_lease",
                        lambda b, k, h, lid: out["release_client"].append((h or {}).get("X-SF-Client")) or True)
    monkeypatch.setattr(studioforge, "vacate_render_leases", lambda *a, **k: False)
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: [])
    monkeypatch.setattr(studioforge, "placement_fits", lambda *a, **k: True)
    monkeypatch.setattr(judge, "select_judge", lambda cfg, ex, override=None: dict(CAND))
    providers.clear_cache()
    yield out
    providers.clear_cache()


@pytest.mark.parametrize("run_holder", ["crucibleforge", "mb-labelled-run"])
def test_judge_lease_is_released_by_its_own_holder(monkeypatch, calls, run_holder):
    monkeypatch.setattr(studioforge, "LEASE_HOLDER", run_holder)
    cfg = _cfg()
    lease = judge.acquire_judge_lease(cfg)
    lease["_provider"].release_lease()
    assert calls["acquire_client"] == ["crucibleforge-judge"]
    assert calls["release_client"] == ["crucibleforge-judge"]
