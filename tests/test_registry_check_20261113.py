"""SYSTEM-45 section 3: the profile's points per item and the lab registry must agree (fails closed)."""
import json

from crucibleforge import registry_check as rc

PROF = {"name": "p", "scores": {
    "work": {"parts": {"a": {"max": 6, "item": "A"}, "b": {"max": 4, "item": "B"}, "pack": {"max": 0}}},
    "chat": {"parts": {"c": {"max": 10, "item": "C"}}}}}


def _reg(tmp_path, items):
    d = tmp_path / "items"
    d.mkdir()
    for name, it in items.items():
        (d / f"{name}.json").write_text(json.dumps(it), encoding="utf-8")
    return rc.load_registry(tmp_path)


def _no_bonus(monkeypatch):
    from crucibleforge import ladder
    monkeypatch.setattr(ladder, "discover", lambda prof: {})


def test_agreeing_profile_and_registry_pass(tmp_path, monkeypatch):
    _no_bonus(monkeypatch)
    reg = _reg(tmp_path, {"A": {"pack": "main", "section": "work", "points": 6, "minutes_measured": 9},
                          "B": {"pack": "main", "section": "work", "points": 4},
                          "C": {"pack": "main", "section": "chat", "points": 10},
                          "OLD": {"pack": "retired", "section": "work", "points": 0}})
    assert rc.check(PROF, reg) == []
    t = rc.table(PROF, reg)
    assert "unmeasured" in t and "minutes: 1 of 2 items measured" in t


def test_every_disagreement_fails_closed(tmp_path, monkeypatch):
    _no_bonus(monkeypatch)
    reg = _reg(tmp_path, {"A": {"pack": "candidate", "section": "chat", "points": 5},
                          "C": {"pack": "main", "section": "chat", "points": 10},
                          "GONE": {"pack": "main", "section": "work", "points": 2}})
    errs = "\n".join(rc.check(PROF, reg))
    assert "A: registry pack 'candidate'" in errs
    assert "A: registry section 'chat'" in errs
    assert "A: registry points 5" in errs
    assert "B: used by work/b but not in the lab registry" in errs
    assert "GONE: registry says pack 'main' but the profile does not use it" in errs


def test_a_scored_part_without_an_item_is_an_error(tmp_path, monkeypatch):
    _no_bonus(monkeypatch)
    prof = {"scores": {"work": {"parts": {"x": {"max": 3}}}}}
    assert rc.check(prof, _reg(tmp_path, {})) == ["work/x: no 'item' naming its lab registry entry"]


def test_minutes_share_over_twice_the_points_share_is_flagged(tmp_path, monkeypatch):
    _no_bonus(monkeypatch)
    reg = _reg(tmp_path, {"A": {"pack": "main", "section": "work", "points": 6, "minutes_measured": 1},
                          "B": {"pack": "main", "section": "work", "points": 4, "minutes_measured": 9},
                          "C": {"pack": "main", "section": "chat", "points": 10, "minutes_measured": 9}})
    t = rc.table(PROF, reg)
    line = next(l for l in t.splitlines() if l.strip().startswith("B "))
    assert "OVER" in line            # 90% of the minutes for 40% of the points
