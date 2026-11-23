"""Fix pass C (B1 to B4): margins for every segment, the missing model named, EV3's reference is the segment marks'
reference, NSFW+ segments carry a 90% margin."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import check_marks45 as CM  # noqa: E402
from test_check_marks45 import DAM, REF, folder, run  # noqa: E402

from crucibleforge import nsfwplus45 as N  # noqa: E402


def test_a_missing_role_names_the_model_it_needs(tmp_path):
    a = folder(tmp_path, "a", REF, {"chat/everyday": (0.9, 0.05)})
    ev = run([a])[0]["everyday"]
    assert "damaged model not on the board" in ev["EV2_damaged_gap"]["reason"]
    assert "split-untied-31b*" in ev["EV2_damaged_gap"]["reason"]
    assert "split-untied-31b*" in ev["EV5_floor_disclosure"]["reason"] or ev["EV5_floor_disclosure"]["status"] != CM.NE


@pytest.mark.shipping_default
def test_ev3_reference_is_the_segment_marks_reference(tmp_path, monkeypatch):
    seen = {}

    def fake_check(runs, marks, roles):
        seen.update(roles)
        return True, ["PASS ok"]
    import check_assistant_marks as CA
    monkeypatch.setattr(CA, "check", fake_check)
    monkeypatch.setattr(CA, "collect", lambda folders, prof: {})
    a = folder(tmp_path, "a", REF, {})
    ev3 = run([a])[0]["everyday"]["EV3_basics_marks"]
    assert seen, ev3["reason"]
    assert seen["reference"] == ["qwen3.8-27b-q8-ref"] and seen["damaged"][0] == "split-untied-31b*"
    CM.evaluate([a], refresh=False, pytest_runner=lambda rel: (True, "1 passed"), reference="other-ref*")
    assert seen["reference"] == ["other-ref*"]


def test_ev1_and_ev2_evaluate_with_a_damaged_anchor(tmp_path):
    r1 = folder(tmp_path, "r1", REF, {"chat/everyday": (0.95, 0.05)})
    r2 = folder(tmp_path, "r2", REF, {"chat/everyday": (0.93, 0.05)})
    d = folder(tmp_path, "d", DAM, {"chat/everyday": (0.5, 0.05)})
    ev = run([r1, r2, d])[0]["everyday"]
    assert ev["EV1_reference"]["status"] != CM.NE and ev["EV2_damaged_gap"]["status"] != CM.NE


def test_nsfw_plus_segments_carry_a_90_margin():
    assert N._se([1, 1, 0, 0]) == pytest.approx((0.25 / 4) ** 0.5)
    g = N._seg("X", 2.0, [("a", 2.0, 0.5, None, 0.1)])
    assert g["margin"] == pytest.approx(1.645 * 0.2)
    assert N._seg("X", 2.0, [("a", 2.0, None, "pending: x")])["margin"] is None
    t = N._total("T", [g, {**g, "margin": 0.3}])
    assert t["margin"] == pytest.approx((0.329 ** 2 + 0.3 ** 2) ** 0.5, abs=1e-3)
