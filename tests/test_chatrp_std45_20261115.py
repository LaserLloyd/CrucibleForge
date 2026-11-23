"""rc4-main's role-play (lead 2026-11-15 02:12, from the pilot table d2-work/logs/table45-nogrok.txt): BOTH sets --
the six RPSESSION cards x 2 writer seeds (3 points) + the eight 4.1 deep role-play cases (2 points), each exactly as
screen-chatrp-pilot ran them. Chat = state 1.5 + tally 1 + know 0.5 + sessions 4 + long scenes 3 = 10 (the owner
removed "Sounds like a real person" from the test, lead 2026-11-15: its 2 went to role-play, 3 + 2 -> 4 + 3).
4.5 rules: a reply cut at its ceiling is ran out (excluded; above a quarter of the part's calls = not measured); empty
after the regenerate-then-thinking-off chain = 0 (a session: its judged half is 0, the code half stands; a deep case:
its unit is 0), never pending. Real rows: tests/fixtures/rc4_roleplay_rows_20261115.json.gz (DeepSeek flash's pilot
rows; Grok's FLAT-chatrp_pilot_s2-004; MiniMax's WR1-ninth-string).
Since 2026-11-15 (lead) the shipping composition judges the sessions on the ladder (test_rpladder_board_20261115) and
the part is validated; the pilot's rubric hand check below reads the part as a composition that selects no judge mode
loads it (``_load_rubric``)."""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from crucibleforge import chains, config, judge_digest, profiles, report, scoring45

pytestmark = pytest.mark.live_ladder

ROWS = json.loads(gzip.open(Path(__file__).parent / "fixtures" / "rc4_roleplay_rows_20261115.json.gz").read())
DEEP = ["RD1-marisol", "RD4-kettle-saddle", "CC3-tea-hut", "CC4-second-pitch", "RV1-three-at-the-table",
        "RV2-skarren-hut", "WR1-ninth-string", "WR2-red-thread-key"]
#: the lead's hand check from the pilot table (DeepSeek flash): sessions piece .896, deep cases .692 -> 4 x .896 + 3 x .692.
#: Grade fix 2026-11-15 (scrub audit items 5-9, bank 86a8038726091d21): the session rows regraded in the fixture
#: (the regrade tool's rows appended, as on disk); code share .942 -> .957, so the piece .896 -> .896 + .5 x .015 = .9035.
#: Audit of f509cec (bank 26eb25bc1caafcd3): seed 1 ferry canon2@8 (the drowning rebutted) now passes -> .9059
#: Long-scene fix 2026-11-15 (scrub items 10-12 + RV2 N1, bench45 quote_own_turns + rpdeep_overrides): mean case
#: rate .6915 -> .6686 (RD1 N-late-callback, RV1 C1 and L1 quote another turn -> no; CC3 W2, RD4 N2, RV2 N1 unscored)
#: Long-scene D1 (b) 2026-11-19 (lane-j): a fact group is zeroed out of character only on a code reason, so CC3's
#: judge-flagged groups keep their verdict: mean case rate .6686 -> .6984
HAND = {"j_rpsession": 4 * 0.9059, "j_rplong": 3 * 0.6984}


def _load(comp):
    mp = pytest.MonkeyPatch()
    mp.setenv("CF_COMPOSITION", comp)
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    mp.undo()
    return p


def _load_rubric():
    """``standard`` with its judge_mode_select dropped: the sessions' rubric mode exactly as piloted."""
    mp = pytest.MonkeyPatch()
    orig = profiles._select_judge_modes

    def no_select(out, name, want):
        out.pop("judge_mode_select", None)
        return orig(out, name, want)
    mp.setattr(profiles, "_select_judge_modes", no_select)
    try:
        return _load("standard")
    finally:
        mp.undo()


@pytest.fixture
def rc4(monkeypatch):
    # per test: the conftest restores the case root, the active profile and the speaker-tag switch after each one
    monkeypatch.setenv("CF_COMPOSITION", "rc4-main")
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    # lane-j: a judged part also waits for its second-reader marks; here the sessions' marks pass, long scenes have none
    from crucibleforge.agree_marks import marks_sha256
    monkeypatch.setattr(report, "judge_marks_result", lambda: {"marks_sha256": marks_sha256(),
                                                               "parts": {"chat/j_rpsession": {"pass": True}}})
    # these tests check the points, not coverage: every model here counts as covered (test_judge_marks_per_model)
    monkeypatch.setattr(report, "agreement_covers", lambda *a, **k: True)
    return p


def _stamped(rows):
    """The cut's `judge_stamp_required`: an unstamped verdict is stale, and the pilot rows predate the stamp. Stamp
    each verdict as judge.py does after its one clean judge pass (the current rubric, the profile judge's thinking),
    so these tests read the marks the pilot judge gave."""
    rows = copy.deepcopy(rows)
    for r in rows:
        if judge_digest.stale_reason(r) == "unstamped":
            for thinking in (False, True):
                r["judge"]["instrument"] = judge_digest.instrument(r, "pilot-judge", thinking)
                if judge_digest.stale_reason(r) is None:
                    break
            assert judge_digest.stale_reason(r) is None, r["case_id"]
    return rows


def _stats(p, rows):
    cfg = {"_profile": "bench45", "models": []}
    rows = report.board_filter("toy", cfg, rows=config.dedupe_rows(_stamped(rows)))
    s = report.stats_from_tier_rows("toy", cfg, p, {1: rows}, {}, None)
    s["scorecard"] = report.tier_scorecard(s, report.scoring_config(cfg), p)
    return s


def test_chat_sums_to_ten_with_both_role_play_sets(rc4):
    parts = rc4["scores"]["chat"]["parts"]
    # integration 2026-11-20: three max-0 pack parts (chat/pk_*, packs45) carry the Chat packs; they add no points
    assert {k: v["max"] for k, v in parts.items()} == {"h2_state": 1.5, "h2_ledger": 1, "h6_know": 0.5,
                                                        "j_rpsession": 4, "j_rplong": 3, "pk_character": 0,
                                                        "pk_remembers": 0, "pk_cowrites": 0}
    assert sum(v["max"] for v in parts.values()) == 10
    assert parts["j_rpsession"]["label"] == "Role-play: sessions"
    assert parts["j_rplong"]["label"] == "Role-play: long scenes"
    # sessions validated on the ladder (lead 2026-11-15); long scenes still wait for machine checks
    assert parts["j_rpsession"].get("validated", True) is True
    assert report.part_pending_validation(rc4, "chat", "j_rpsession") is None
    assert parts["j_rplong"]["validated"] == "pending-machine"
    assert report.part_pending_validation(rc4, "chat", "j_rplong") == "pending validation (machine checks)"
    assert parts["j_rplong"]["cases"] == DEEP and parts["j_rplong"]["ran_out"] == "exclude"
    assert rc4["cases"]["rpdeep"] == DEEP
    # the nine rc3 role-play cards leave the shipping composition (their files and pins stay)
    assert "j_roleplay" not in parts and "chat/j_roleplay" not in rc4["chains"]


def test_both_sets_are_exactly_the_pilot_and_nothing_else_moves(rc4):
    pilot, rc3 = _load("screen-chatrp-pilot"), _load("rc3-main")
    # the shipping composition swaps only the judge mode (the ladder); without the selection it is the pilot's
    assert {k: v for k, v in rc4["chains"]["chat/j_rpsession"].items() if k != "judge_mode"} == \
        {k: v for k, v in pilot["chains"]["chat/j_rpsession"].items() if k != "judge_mode"}
    assert rc4["chains"]["chat/j_rpsession"]["judge_mode"]["kind"] == "ladder"
    assert _load_rubric()["chains"]["chat/j_rpsession"] == pilot["chains"]["chat/j_rpsession"]
    jm = _load_rubric()["chains"]["chat/j_rpsession"]["judge_mode"]
    assert jm["combine"] == "weakest" and jm["code_weight"] == 0.5
    assert jm["axes"] == ["prose", "dialogue", "emotion", "atmosphere"]
    sec = rc4["chains"]["chat/j_rpsession"]
    assert sec["se_unit"] == "prompt" and sec["se_cluster"] == "card"      # margins: cards (both seeds together)
    assert rc4["scores"]["chat"]["parts"]["j_rplong"]["cases"] == pilot["scores"]["chat"]["parts"]["deep41"]["cases"]
    assert pilot["cases"]["rpdeep"] == DEEP
    for k, v in rc3["chains"].items():
        if k not in ("chat/j_roleplay", "chat/j_person") and k.startswith(("work/", "chat/")):   # the adult page is rc4's own
            assert rc4["chains"][k] == v, k
    # (the three Assistant basics categories are the standard composition's own addition, 2026-11-19; they are in no score)
    assistant = ("instruct", "reasoning", "longctx")
    # (the four fix units are the standard composition's own addition too, 2026-11-19: "Fixes and changes existing code")
    # (step 2, 2026-11-21: the generated units are patchmini L1-3, patchfix L1-4, patchmod L3)
    from crucibleforge import profiles as _pr
    fixes = {"codeagent": ["CB-F1-larder-bughunt"],
             "coding": [c["id"] for c in _pr.core_generated_cases(rc4)]}
    got = {k: v for k, v in rc4["cases"].items() if k != "rpdeep" and k not in assistant}
    assert {k: got.pop(k) for k in list(fixes)} == fixes
    assert got == {k: v for k, v in rc3["cases"].items()}
    assert [i for c in assistant for i in rc4["cases"][c]] == rc4["assistant"]["cases"]
    for k in ("h2_state", "h2_ledger", "h6_know"):
        assert rc4["scores"]["chat"]["parts"][k]["max"] == rc3["scores"]["chat"]["parts"][k]["max"]


def test_deepseek_flash_pilot_rows_give_the_hand_computed_points(rc4):
    s = _stats(_load_rubric(), ROWS["deepseek-flash"])
    uv = s["scorecard"]["unvalidated"]["chat"]
    got = {"j_rpsession": s["scorecard"]["parts"]["chat"]["j_rpsession"], "j_rplong": uv["j_rplong"]["points"]}
    for k, want in HAND.items():
        assert got[k] == pytest.approx(want, abs=0.002), (k, got[k])
    assert sum(got.values()) == pytest.approx(4 * 0.9059 + 3 * 0.6984, abs=0.005)    # 5.72
    # long scenes: shown with its points, never counted while pending validation
    assert s["scorecard"]["parts"]["chat"]["j_rplong"] is None and "j_rpsession" not in uv
    cs = s["chain_stats"]["chat/j_rpsession"]
    assert cs["units"] == 12 and cs["complete"]
    assert s["subset_stats"]["chat/j_rplong"]["ran_out_case"]["calls"] == 8


def _grok_unit(rows):
    p = _load("rc4-main")
    st = chains.section_stats([r for r in config.dedupe_rows(copy.deepcopy(rows)) if r.get("chain")], p, None)
    return st["chat/j_rpsession"]["per_chain"]["FLAT-chatrp_pilot_s2-004"]


def test_a_session_empty_after_the_chain_scores_its_code_half_never_pending():
    """Grok FLAT-chatrp_pilot_s2-004 (RPS4 ferry): turn 10 empty on every step of the regenerate chain (first try,
    regenerate, thinking off). The piece is .5 x 0 + .5 x its code share (.76), not excluded, not pending."""
    rows = ROWS["grok-4-7"]
    t10 = [r for r in rows if r.get("turn") == 10 and r.get("empty_stop")]
    assert t10 and all(len(r["empty_stop_retry"]["chain"]) == 3 for r in t10)
    assert any((r.get("judge") or {}).get("empty_generation") for r in t10)
    u = _grok_unit(rows)
    (el,) = u["elements"]
    assert u["ended"] and not u.get("pending_judge")
    # the session's code share is .7647 (26 of 34 check elements): piece .5 x 0 + .5 x .7647
    assert el["outcome"] == "empty" and el["score"] == pytest.approx(0.5 * 0.7647, abs=1e-3)
    # the whole-piece 0 it was before (the code half thrown away too)
    dec = {"id": "x-jrubric", "score": 0.0, "source": "judge", "outcome": "empty"}
    got = chains.empty_piece(dec, {"score": 0.7647}, {"code_weight": 0.5})
    assert got["score"] == pytest.approx(0.38235) and got["judge_share"] == 0.0 and got["code_share"] == 0.7647
    for other in ("degenerate", "ran out", "refused"):
        assert chains.empty_piece({**dec, "outcome": other}, {"score": 0.7647}, {"code_weight": 0.5})["score"] == 0.0
    assert chains.empty_piece(dec, {"score": 0.7647}, {"kind": "rubric"})["score"] == 0.0   # no code_weight


def test_judge_not_yet_run_stays_pending_but_a_reply_never_judged_and_empty_is_still_zero():
    """The distinction that decides 0 against pending: a session row whose judge has not run (no ``judge`` key) is
    pending; the empty-after-the-chain unit is decided from the reply itself, verdict or not."""
    rows = copy.deepcopy(ROWS["grok-4-7"])
    for r in rows:
        r.pop("judge", None)
    u = _grok_unit(rows)
    assert u["elements"][0]["outcome"] == "empty" and u["ended"]
    # an ordinary session with its verdict stripped waits for the judge
    ds = [r for r in ROWS["deepseek-flash"] if r["case_id"].startswith("FLAT-chatrp_pilot_s1-001")]
    ds = copy.deepcopy(ds)
    for r in ds:
        r.pop("judge", None)
    st = chains.section_stats([r for r in config.dedupe_rows(ds) if r.get("chain")], _load("rc4-main"), None)
    assert st["chat/j_rpsession"]["per_chain"]["FLAT-chatrp_pilot_s1-001"].get("pending_judge")


def test_a_deep_case_cut_at_its_ceiling_is_excluded(rc4):
    """MiniMax WR1-ninth-string: turn 3 cut at 16,384 with no text (the pilot scored it 0). Under the 4.5 rule the
    job is ran out: left out of the mean, one of the part's eight calls; above a quarter = not measured."""
    mm = ROWS["minimax-m3-1-flash"]
    assert any(r["finish_reason"] == "length" for r in mm)
    others = [r for r in ROWS["deepseek-flash"] if r["case_id"] in DEEP and r["case_id"] != "WR1-ninth-string"]
    rows = config.dedupe_rows(copy.deepcopy(others + mm))
    kept, rec = report.case_part_rows(rows, "exclude")
    assert rec["calls"] == 8 and rec["ran_out"] == 1 and not rec["not_measured"]
    assert not any(r["case_id"] == "WR1-ninth-string" for r in kept)
    assert len(kept) == len(rows) - len([r for r in rows if r["case_id"] == "WR1-ninth-string"])
    # three of eight cut: not measured
    cut = copy.deepcopy(rows)
    for r in cut:
        if r["case_id"] in ("RD1-marisol", "CC3-tea-hut") and r.get("needs_judge"):
            r["finish_reason"], r["truncated"] = "length", True
    kept, rec = report.case_part_rows(config.dedupe_rows(cut), "exclude")
    assert rec["ran_out"] == 3 and rec["not_measured"] and kept == []
    # without the rule nothing moves (4.1 / rc3 parts)
    assert report.case_part_rows(rows, None) == (rows, None)


def test_a_deep_case_empty_after_the_chain_is_zero_not_missing(rc4):
    rows = copy.deepcopy(ROWS["deepseek-flash"])
    fin = [r for r in rows if r["case_id"] == "WR2-red-thread-key" and r.get("needs_judge")]
    assert fin
    chain = {"mode": "regenerate_then_thinking_off",
             "chain": [{"mode": m, "visible": False, "reasoning_chars": 0} for m in ("first", "regenerate",
                                                                                    "thinking_off")]}
    for r in fin:
        r.update(response="", finish_reason="stop", truncated=False, reasoning="", metrics={},
                 empty_stop={"reasoning_chars": 0, "history": "empty"}, empty_retry=True, empty_stop_retry=chain,
                 judge={"judge_failed": True, "empty_generation": True, "judge_raw": "", "refused": False,
                        "scores": None})
    assert scoring45.job_missing_reason(fin) is None
    s = _stats(rc4, rows)
    sub = s["subset_stats"]["chat/j_rplong"]
    assert sub["rpdeep"]["per_case"]["WR2-red-thread-key"] == 0
    assert not [m for m in s["missing_jobs"] if m["case_id"] == "WR2-red-thread-key"]
    # the same empty reply with no chain on record is a dropped stream: missing, re-run
    for r in fin:
        r.pop("empty_stop_retry")
    assert scoring45.job_missing_reason(fin) == "empty"


def test_the_shipping_composition_has_no_real_person_part(tmp_path, monkeypatch, capsys):
    """Owner 2026-11-15 (lead): "Sounds like a real person" is not part of the test. `standard` (what a bare command
    runs) has no j_person part, no chat/j_person chain, schedules no real-person card in `all --plan`, and Chat sums
    to 10 with role-play 4 + 3. The card files, pins and stored rows stay (rc3-main still declares the part)."""
    monkeypatch.delenv("CF_COMPOSITION", raising=False)
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    assert p["composition"] == "standard"
    parts = p["scores"]["chat"]["parts"]
    assert "j_person" not in parts and "chat/j_person" not in p["chains"]
    assert sum(v["max"] for v in parts.values()) == 10
    assert (parts["j_rpsession"]["max"], parts["j_rplong"]["max"]) == (4, 3)
    assert not [s for s in chains.sections(p) if "person" in s["part"]]
    blob = json.dumps({k: v for k, v in p.items() if k != "compositions"})
    assert "rc3_person" not in blob and "JCARD-PERSON" not in blob and "real_person" not in blob
    assert "j_person" in _load("rc3-main")["scores"]["chat"]["parts"]          # the part itself is kept elsewhere
    # the run plan: a hosted toy model (nothing is called; --plan only prints)
    cfg = tmp_path / "models.yaml"
    cfg.write_text("providers:\n  toy:\n    type: openai\n    base_url: http://127.0.0.1:9/v1\n"
                   "    api_key_env: CF_TOY_KEY_UNSET\n    concurrency: 1\n"
                   "models:\n- name: toy\n  provider: toy\n  model_id: toy-1\n  context_length: 131072\n"
                   "judge:\n  candidates:\n  - provider: toy\n    model_id: toy-1\n    context_length: 16384\n"
                   "defaults:\n  context_length: 131072\n", encoding="utf-8")
    monkeypatch.setenv("CRUCIBLEFORGE_RESULTS", str(tmp_path / "results"))
    from crucibleforge import cli
    try:
        rc = cli.main(["--config", str(cfg), "all", "--profile", "bench45", "--models", "toy", "--plan"])
    except SystemExit as e:
        rc = e.code
    assert rc in (0, None)
    out = capsys.readouterr().out
    assert "FLAT-chatrp_pilot_s2-006" in out and "WR2-red-thread-key" in out      # role-play is scheduled
    assert "person" not in out.lower()                                              # real person is not
