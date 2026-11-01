"""A run that never loaded or leased anything on the rig (hosted models only)
restores no residents: on 2026-10-29 an API-only MiniMax run reloaded an
embedder the nightly had evicted, at 02:37, inside the rig's quiet hours."""
from crucibleforge import providers, studioforge


def _calls(monkeypatch, now=()):
    calls = []
    monkeypatch.setattr(studioforge, "release_lease", lambda *a, **k: True)
    monkeypatch.setattr(studioforge, "residents",
                        lambda *a, **k: [{"model_id": m} for m in now])
    monkeypatch.setattr(studioforge, "_mgmt",
                        lambda *a, **k: calls.append(a[4]) or (200, {}))
    monkeypatch.setattr(studioforge, "unload_all", lambda *a, **k: calls.append("unload"))
    return calls


def test_an_untouched_rig_gets_no_reload(monkeypatch):
    calls = _calls(monkeypatch)
    p = providers.Provider(name="sf", type="studioforge", base_url="http://x/v1")
    p.restore([{"model_id": "embedder", "loaded_by": "jit:/v1/embeddings"}])
    assert calls == []


def test_a_rig_this_run_used_still_gets_its_residents_back(monkeypatch):
    calls = _calls(monkeypatch)
    p = providers.Provider(name="sf", type="studioforge", base_url="http://x/v1")
    p._touched = True
    p.restore([{"model_id": "embedder", "loaded_by": "jit:/v1/embeddings"}])
    assert calls == ["/api/models/embedder/load"]
