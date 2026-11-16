"""A part whose points do not count can never read MAXED and can never open a pack (lead 2026-11-15).

``validated: false | pending-machine | pending-owner`` keeps a part's points out of the number. Before this,
the part's own packs still opened on its share (the gate was taken before the flag) and its MAXED mark was
computed from that same gate, so a part that counts nothing could unlock harder questions or read MAXED."""
from __future__ import annotations

import copy

import pytest

from crucibleforge import config, ladder, profiles, report

pytestmark = pytest.mark.live_ladder
CFG = config.load_config(config.EXAMPLE_CONFIG_PATH)


@pytest.fixture(scope="module")
def prof():
    """The shipping composition (rc4-main; `standard` at the cut): Main parts with packs of their own."""
    mp = pytest.MonkeyPatch()
    mp.setenv(profiles.COMPOSITION_ENV, "rc4-main")
    try:
        yield profiles.load_profile("bench45", CFG)
    finally:
        mp.undo()


@pytest.fixture(autouse=True)
def _rc4(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc4-main")


def _card(prof, share):
    def fake(src, w, partial=frozenset()):
        out = {"chat": None, "coding": None, "total": None, "tok_per_s": None,
               "components": {}, "weights": dict(w)}
        out.update({g: share for g in w})
        return out
    mp = pytest.MonkeyPatch()
    mp.setattr(report, "scorecard", fake)
    try:
        sc = report._declared_weights(prof, None, None)[0]
        # chain parts read their share from chain_stats (rc4: every Main part is a chain unit)
        src = {"chain_stats": {f"{s}/{k}": {"share": share / 100.0, "complete": True, "mean": share / 100.0}
                               for s, d in prof["scores"].items() for k in (d.get("parts") or {})}}
        return report.points_scorecard(src, sc, prof)
    finally:
        mp.undo()


def _flag(prof, score, pk, how="pending-machine"):
    p = copy.deepcopy(prof)
    p["scores"][score]["parts"][pk]["validated"] = how
    return p


def _parts(prof):
    for score, d in (prof.get("scores") or {}).items():
        for pk, q in (d.get("parts") or {}).items():
            if q and float(q.get("max") or 0) > 0 and not q.get("unlock_part") and q.get("validated") in (None, True):
                yield score, pk


def test_a_flagged_part_never_opens_its_packs(prof):
    sets = ladder.discover(prof)
    score, pk = next((s, k) for s, k in _parts(prof) if f"{s}/{k}" in sets)
    det = _card(prof, 100.0)["ladder"][score][pk]
    assert any(x["state"] != "locked" for x in det["sets"]), det["sets"]        # the control opens
    for how in ("pending-machine", "pending-owner", False):
        det = _card(_flag(prof, score, pk, how), 100.0)["ladder"][score][pk]
        assert all(x["state"] == "locked" for x in det["sets"]), (how, det["sets"])
        assert det["unlock_share"] is None and not det.get("opened") and det["ladder_unrun"] == 0


def test_a_flagged_part_never_reads_maxed(prof):
    sets = ladder.discover(prof)
    score, pk = next((s, k) for s, k in _parts(prof) if f"{s}/{k}" not in sets)
    assert _card(prof, 100.0)["ladder"][score][pk]["maxed"] is True               # the control maxes
    card = _card(_flag(prof, score, pk), 100.0)
    assert card["ladder"][score][pk]["maxed"] is False
    assert pk not in ((card.get("maxed") or {}).get(score) or {}).get("parts", [])
