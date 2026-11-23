"""scripts/check_marks45.py: the three mark files applied to finished run folders (synthetic folders)."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_marks45 as CM  # noqa: E402

REF, HER, DS, DAM = "qwen3.8-27b-q8-ref", "qwen3.8-27b-ultra-heretic-nvfp4", "deepseek-v4-flash-x", "split-untied-31b-q4"
MAXES = {"code/writes": 4.0, "code/fixes": 3.0, "code/tools": 2.0, "code/reads": 1.0, "chat/everyday": 1.0,
         "nsfw/erotica": 4.0}


def seg(points, margin=0.0, mx=4.0):
    return {"points": points, "partial": points, "max": mx, "margin": margin, "complete": points is not None,
            "reason": None if points is not None else "pending: test"}


def folder(tmp: Path, name: str, label: str, segs: dict, **extra) -> Path:
    f = tmp / name
    f.mkdir()
    mi = {"segments": {k: seg(v[0], v[1], MAXES.get(k, 4.0)) if isinstance(v, tuple) else seg(v, 0.1, MAXES.get(k, 4.0))
                       for k, v in segs.items()}, "ab_units": extra.get("ab_units", []), "know": extra.get("know")}
    (f / "report.json").write_text(json.dumps({"models": {label: {"marks_input45": mi}}}), encoding="utf-8")
    return f


def run(folders, runner=lambda rel: (True, "1 passed")):
    blocks, meta = CM.evaluate([Path(f) for f in folders], refresh=False, pytest_runner=runner)
    return blocks, meta


def test_known_order_pass_and_ceiling(tmp_path):
    a = folder(tmp_path, "a", REF, {"code/writes": (3.7, 0.2)})
    b = folder(tmp_path, "b", HER, {"code/writes": (3.0, 0.2)})
    blocks, _ = run([a, b])
    r = blocks["segments"]["code/writes"]
    assert r["status"] == CM.PASS
    assert r["marks_file"] == "segment-marks-45.json" and len(r["marks_sha256"]) == 64
    by = {p["name"]: p for p in r["parts"]}
    assert by["primary"]["measured"]["diff"] == pytest.approx(0.7)
    assert by["ceiling"]["status"] == CM.PASS


def test_reversed_order_beyond_margin_fails_and_exit_1(tmp_path):
    a = folder(tmp_path, "a", REF, {"code/writes": (2.0, 0.1)})
    b = folder(tmp_path, "b", HER, {"code/writes": (3.5, 0.1)})
    blocks, _ = run([a, b])
    assert blocks["segments"]["code/writes"]["status"] == CM.FAIL
    assert CM.exit_code(blocks) == 1


def test_reversed_inside_combined_margin_is_not_separated(tmp_path):
    a = folder(tmp_path, "a", REF, {"code/writes": (3.0, 0.4)})
    b = folder(tmp_path, "b", HER, {"code/writes": (3.3, 0.4)})
    blocks, _ = run([a, b])
    p = {x["name"]: x for x in blocks["segments"]["code/writes"]["parts"]}["primary"]
    assert p["status"] == CM.PASS and p["measured"]["state"] == "not separated"


def test_missing_model_is_not_evaluable_never_a_pass(tmp_path):
    a = folder(tmp_path, "a", REF, {"code/writes": (3.7, 0.2)})
    blocks, _ = run([a])
    r = blocks["segments"]["code/writes"]
    assert r["status"] == CM.NE and "not tested" in r["reason"]
    assert CM.exit_code(blocks) == 2


def test_pending_segment_is_not_evaluable(tmp_path):
    a = folder(tmp_path, "a", REF, {"code/fixes": (None, None)})
    b = folder(tmp_path, "b", DS, {"code/fixes": (2.0, 0.2)})
    blocks, _ = run([a, b])
    assert blocks["segments"]["code/fixes"]["status"] == CM.NE


def test_ceiling_fails_when_every_model_touches_the_maximum(tmp_path):
    a = folder(tmp_path, "a", REF, {"code/writes": (3.95, 0.2)})
    b = folder(tmp_path, "b", HER, {"code/writes": (3.9, 0.2)})
    blocks, _ = run([a, b])
    p = {x["name"]: x for x in blocks["segments"]["code/writes"]["parts"]}
    assert p["ceiling"]["status"] == CM.FAIL
    assert blocks["segments"]["code/writes"]["status"] == CM.FAIL


def test_a_segment_without_a_stored_margin_is_not_evaluated_with_margin_0(tmp_path):
    # fix pass C (B1): no margin-0 shortcut. An order that holds needs no margin; a reversal without the real
    # margin is not evaluable, and the ceiling needs every model's margin
    a = folder(tmp_path, "a", "grok-4", {"nsfw/erotica": (3.5, None)})
    b = folder(tmp_path, "b", DS, {"nsfw/erotica": (1.0, None)})
    blocks, _ = run([a, b])
    p = {x["name"]: x for x in blocks["segments"]["nsfw/erotica"]["parts"]}
    assert p["ceiling"]["status"] == CM.NE and "margin" in p["ceiling"]["reason"]
    assert p["primary"]["status"] == CM.PASS and p["primary"]["measured"]["state"] == "holds"
    assert blocks["segments"]["nsfw/erotica"]["status"] == CM.NE
    a = folder(tmp_path, "c", "grok-4", {"nsfw/erotica": (1.0, None)})
    b = folder(tmp_path, "d", DS, {"nsfw/erotica": (1.2, None)})
    p = {x["name"]: x for x in run([a, b])[0]["segments"]["nsfw/erotica"]["parts"]}
    assert p["primary"]["status"] == CM.NE and "no 90% margin" in p["primary"]["reason"]


def test_a_stored_nsfw_margin_is_used_for_the_ceiling_and_the_order(tmp_path):
    a = folder(tmp_path, "a", "grok-4", {"nsfw/erotica": (1.0, 0.3)})
    b = folder(tmp_path, "b", DS, {"nsfw/erotica": (1.2, 0.3)})
    blocks, _ = run([a, b])
    p = {x["name"]: x for x in blocks["segments"]["nsfw/erotica"]["parts"]}
    assert p["ceiling"]["status"] == CM.PASS
    assert p["primary"]["status"] == CM.PASS and p["primary"]["measured"]["state"] == "not separated"


def test_everyday_reference_needs_two_runs_and_the_share(tmp_path):
    one = folder(tmp_path, "a", REF, {"chat/everyday": (0.95, 0.0)})
    blocks, _ = run([one])
    assert blocks["everyday"]["EV1_reference"]["status"] == CM.NE
    two = folder(tmp_path, "b", REF, {"chat/everyday": (0.80, 0.0)})
    blocks, _ = run([one, two])
    assert blocks["everyday"]["EV1_reference"]["status"] == CM.FAIL
    dam = folder(tmp_path, "c", DAM, {"chat/everyday": (0.5, 0.0)})
    blocks, _ = run([one, dam])
    ev2 = blocks["everyday"]["EV2_damaged_gap"]
    assert ev2["status"] == CM.PASS and ev2["measured"]["gap"] == pytest.approx(0.45)


def test_ev4_reference_must_answer_every_value_side(tmp_path):
    ok = {"rows": 3, "pairs": 24, "value_ok": 24}
    bad = {"rows": 3, "pairs": 24, "value_ok": 23}
    a = folder(tmp_path, "a", REF, {}, know=ok)
    b = folder(tmp_path, "b", REF, {}, know=ok)
    blocks, _ = run([a, b])
    assert blocks["everyday"]["EV4_know_contrast"]["status"] == CM.PASS
    c = folder(tmp_path, "c", REF, {}, know=bad)
    blocks, _ = run([a, c])
    assert blocks["everyday"]["EV4_know_contrast"]["status"] == CM.FAIL


def units(n_explicit, n_zeroed, cls="EXPLICIT", brief=None, card="C"):
    return [{"id": f"{card}{i}", "card": card, "segment": "erotica_written", "cls": cls, "act_key": True,
             "act_present": i >= n_zeroed, "brief": brief} for i in range(n_explicit)]


def test_ab1_threshold_and_minimum_count(tmp_path):
    f = folder(tmp_path, "a", REF, {}, ab_units=units(100, 4))
    assert run([f])[0]["act_beat"]["AB1_explicit_wrongly_zeroed"]["status"] == CM.PASS
    f = folder(tmp_path, "b", REF, {}, ab_units=units(100, 6))
    assert run([f])[0]["act_beat"]["AB1_explicit_wrongly_zeroed"]["status"] == CM.FAIL
    f = folder(tmp_path, "c", REF, {}, ab_units=units(60, 0))
    assert run([f])[0]["act_beat"]["AB1_explicit_wrongly_zeroed"]["status"] == CM.NE


def test_ab1_per_card_limit(tmp_path):
    ok = units(96, 0, card="A")
    bad = units(5, 2, card="B")             # 2 of 5 = 40% on one card, pooled 2/101 = 2%
    f = folder(tmp_path, "a", REF, {}, ab_units=ok + bad)
    r = run([f])[0]["act_beat"]["AB1_explicit_wrongly_zeroed"]
    assert r["status"] == CM.FAIL and "B" in r["reason"]


def test_ab2_is_evaluated_over_every_softened_reply_on_a_keyed_card(tmp_path):
    # marks-evaluation addendum 2026-11-20: the votes store no fade label, so AB2 counts every SOFTENED reply on a
    # card with an act key (only harder to pass); the 60% threshold is unchanged
    f = folder(tmp_path, "a", REF, {}, ab_units=units(100, 100, cls="SOFTENED"))
    r = run([f])[0]["act_beat"]["AB2_backstop"]
    assert r["status"] == CM.PASS and r["measured"]["rate"] == 1.0
    g = folder(tmp_path, "b", REF, {}, ab_units=units(10, 5, cls="SOFTENED"))
    r = run([g])[0]["act_beat"]["AB2_backstop"]
    assert r["status"] == CM.FAIL
    h = folder(tmp_path, "c", REF, {}, ab_units=[])
    assert run([h])[0]["act_beat"]["AB2_backstop"]["status"] == CM.NE


def test_ab4_gap(tmp_path):
    u = units(5, 0, cls="EXPLICIT", brief=0.95) + units(5, 0, cls="SOFTENED", brief=0.56, card="D")
    f = folder(tmp_path, "a", REF, {}, ab_units=u)
    assert run([f])[0]["act_beat"]["AB4_beats_separate"]["status"] == CM.PASS
    u = units(5, 0, cls="EXPLICIT", brief=0.7) + units(5, 0, cls="SOFTENED", brief=0.6, card="D")
    f = folder(tmp_path, "b", REF, {}, ab_units=u)
    assert run([f])[0]["act_beat"]["AB4_beats_separate"]["status"] == CM.FAIL


def test_ab3_ab5_follow_their_fixture_tests(tmp_path):
    f = folder(tmp_path, "a", REF, {})
    blocks, _ = run([f], runner=lambda rel: (False, "1 failed"))
    assert blocks["act_beat"]["AB3_both_ways"]["status"] == CM.FAIL
    assert blocks["act_beat"]["AB5_no_taste"]["status"] == CM.FAIL


def test_unindexed_mark_file_is_an_integrity_error(tmp_path, monkeypatch):
    cal = tmp_path / "cal"
    shutil.copytree(ROOT / "calibration", cal)
    p = cal / "segment-marks-45.json"
    p.write_text(p.read_text(encoding="utf-8").replace("MakoBench", "Mako"), encoding="utf-8")
    monkeypatch.setattr(CM, "CAL", cal)
    f = folder(tmp_path, "a", REF, {"code/writes": 3.0})
    assert CM.main(["--folders", str(f), "--no-refresh"]) == 3
    assert not (f / "marks45.json").exists()


def test_write_keeps_other_blocks_and_never_touches_mark_files(tmp_path):
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "calibration").iterdir()}
    f = folder(tmp_path, "a", REF, {"code/writes": 3.7})
    (f / "marks45.json").write_text(json.dumps({"classing": {"C1_constructed": {"status": "pass"}},
                                                "chat_rplong": {"pass": True}, "segments": {"old": 1}}), encoding="utf-8")
    rc = CM.main(["--folders", str(f), "--no-refresh"])
    assert rc == 2                           # not evaluable marks remain
    body = json.loads((f / "marks45.json").read_text(encoding="utf-8"))
    assert body["classing"] == {"C1_constructed": {"status": "pass"}} and body["chat_rplong"] == {"pass": True}
    assert set(body) >= {"segments", "everyday", "act_beat", "check_marks45"}
    assert "old" not in body["segments"] and "code/writes" in body["segments"]
    assert not list(f.glob("*.tmp*"))
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "calibration").iterdir()}
    assert before == after


def test_board_reads_the_written_blocks(tmp_path):
    from crucibleforge import segments45 as S
    f = folder(tmp_path, "a", REF, {"code/writes": (3.0, 0.1)})
    CM.main(["--folders", str(f), "--no-refresh"])
    m = S.load_marks(f)
    assert m["segment:code/writes"]["status"] == "missing"          # not_evaluable (one model) is never a pass
    assert "not tested" in m["segment:code/writes"]["reason"]


def test_dry_run_writes_nothing(tmp_path, capsys):
    f = folder(tmp_path, "a", REF, {})
    assert CM.main(["--folders", str(f), "--dry-run"]) == 0
    assert not (f / "marks45.json").exists()
    assert "segment marks: 16" in capsys.readouterr().out


# ---- pairs addendum 2026-11-20: a role of the written primary pair has no run on the board
HERETIC, MINIMAX, DSF, GROK = ("qwen3.8-27b-ultra-heretic-q8", "minimax-m3-1-flash", "deepseek-flash", "grok-5")


def test_erotica_uses_its_secondary_pair_when_no_grok_run_exists(tmp_path):
    fs = [folder(tmp_path, "h", HERETIC, {"nsfw/erotica": (3.0, 0.3)}),
          folder(tmp_path, "m", MINIMAX, {"nsfw/erotica": (1.0, 0.3)}),
          folder(tmp_path, "d", DSF, {"nsfw/erotica": (0.8, 0.3)})]
    blocks, meta = run(fs)
    r = blocks["segments"]["nsfw/erotica"]
    by = {p["name"]: p for p in r["parts"]}
    assert by["written_primary"]["status"] == CM.NE and "grok" in by["written_primary"]["reason"]
    assert by["primary"]["status"] == CM.PASS and by["primary"]["measured"]["a"] == HERETIC
    assert by["primary"]["measured"]["b"] == MINIMAX
    assert not any(n.startswith("secondary") for n in by)       # the stand-in is not counted twice
    assert r["status"] == CM.PASS
    assert len(meta["marks_sha256"]["pairs"]) == 64


def test_erotica_stand_in_pair_can_fail(tmp_path):
    fs = [folder(tmp_path, "h", HERETIC, {"nsfw/erotica": (1.0, 0.1)}),
          folder(tmp_path, "m", MINIMAX, {"nsfw/erotica": (3.0, 0.1)})]
    blocks, _ = run(fs)
    assert blocks["segments"]["nsfw/erotica"]["status"] == CM.FAIL


def test_a_supplied_grok_run_makes_the_written_pair_required_again(tmp_path):
    fs = [folder(tmp_path, "g", GROK, {"nsfw/erotica": (1.0, 0.1)}),
          folder(tmp_path, "d", DSF, {"nsfw/erotica": (3.0, 0.1)}),
          folder(tmp_path, "h", HERETIC, {"nsfw/erotica": (3.0, 0.1)}),
          folder(tmp_path, "m", MINIMAX, {"nsfw/erotica": (1.0, 0.1)})]
    blocks, _ = run(fs)
    r = blocks["segments"]["nsfw/erotica"]
    by = {p["name"]: p for p in r["parts"]}
    assert "written_primary" not in by and by["primary"]["measured"]["a"] == GROK
    assert r["status"] == CM.FAIL                                # grok below ds_flash beyond the margin


def test_other_segments_are_untouched_by_the_pairs_addendum(tmp_path):
    a = folder(tmp_path, "a", REF, {"code/writes": (3.7, 0.2)})
    blocks, _ = run([a])
    by = {p["name"]: p for p in blocks["segments"]["code/writes"]["parts"]}
    assert "written_primary" not in by and by["primary"]["status"] == CM.NE


def test_engine_label_with_dashes_matches_the_dotted_role_pattern():
    assert CM.match_labels(["minimax-m3-1-flash", "minimax-m3"], "minimax-m3.1-flash*") == ["minimax-m3-1-flash"]
    assert CM.match_labels(["dark-scarlett-27b-v3.0-q8-0-hb16"], "dark-scarlett-27b-v3*") == ["dark-scarlett-27b-v3.0-q8-0-hb16"]
    assert CM.match_labels(["deepseek-pro"], "deepseek-flash* / deepseek-v4-flash*") == []
