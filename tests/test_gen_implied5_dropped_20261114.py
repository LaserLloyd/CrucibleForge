"""IMPLIED level 5: checks the brief contradicts or never implies are not scored (scrub review, 2026-11-14).

meds3 `critcap` keyed a 4th push inside 60 minutes, against the brief's only stated rule "at most 3 notifications in any
rolling 60 minutes" (severity is never given a meaning). car3 `onecar` / `onecarex` rest on a one-car-per-member rule the
brief never states (two requests with their own ids each get a car). Dropped like wh3's DROPPED: kept in build() so every
other check's scenario stays byte-identical; the prompt does not change."""
import pytest

from crucibleforge.gen import implied as im


def _seeds(dom, n=3):
    out, s = [], 0
    while len(out) < n and s < 200:
        s += 1
        if im.make_instance(s, 5)["public"]["domain"] == dom:
            out.append(s)
    return out


@pytest.mark.parametrize("dom,gone", [("meds3", {"critcap"}), ("car3", {"onecar", "onecarex"})])
def test_dropped_checks_are_not_scored(dom, gone):
    for s in _seeds(dom):
        inst = im.make_instance(s, 5)
        ids = {e["id"] for e in inst["hidden"]["exps"]}
        assert not ids & gone, (s, ids & gone)
        assert all(r["pass"] for r in im._judge_all(inst, im.reference_code(inst))["results"])


@pytest.mark.parametrize("dom", ["meds3", "car3"])
def test_the_rest_is_unchanged(dom):
    keep = {"meds3": {"x1", "x2", "x3", "yday", "night", "critnight", "plannight", "burst", "nodup", "renag", "taken"},
            "car3": {"x1", "x2", "x3", "cancel", "adjacent", "tzsplit", "multiday", "cent", "fcfs", "fallback", "past", "seats",
                     "sharedcap"}}[dom]
    for s in _seeds(dom):
        assert {e["id"] for e in im.make_instance(s, 5)["hidden"]["exps"]} == keep
