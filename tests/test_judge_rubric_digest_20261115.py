"""Every stored verdict says which rubric it was judged under (lead, 2026-11-15).

CHAT-RP tightened the ``real_person`` rubric text (chat-rp-ship45 7911b8e); result folders hold
verdicts made under the older text and nothing marked them. A verdict now carries ``instrument`` =
{rubric, rubric_sha, judge_model, thinking}; one whose rubric_sha is not the current tree's for its row
(an unstamped verdict counts as made under the freeze baseline's texts) is stale: the board does not
use it (the row reads "judge pending") and the judge pass re-judges it."""
from __future__ import annotations

import json

import pytest

from crucibleforge import config, judge, judge_digest as D, report

NEW_RP_TEXT = judge.REAL_PERSON_TEXT.replace("REAL_PERSON 0-10: does this read",
                                             "REAL_PERSON 0-10: would the person on the other end read")


def _rp_row(**over):
    row = {"bench_run_id": "r1", "case_id": "JC-RP-1", "repeat": 1, "turn": None, "rubric": "real_person",
           "element_standard": 45, "needs_judge": True, "prompt": "p", "response": "ok see u at 6",
           "judge": {"judge_failed": False, "judge_raw": "{}", "refused": False,
                     "scores": {"real_person": 8, "on_topic": 9}, "samples": 1},
           "judge_model": "J", "judge_id": "J"}
    row.update(over)
    return row


@pytest.fixture(autouse=True)
def _fresh_cache():
    D._CACHE.clear()
    yield
    D._CACHE.clear()


def _tighten_real_person(monkeypatch):
    monkeypatch.setattr(judge, "REAL_PERSON_TEXT", NEW_RP_TEXT)
    monkeypatch.setitem(judge.RUBRICS, "real_person", {**judge.RUBRICS["real_person"], "text": NEW_RP_TEXT})
    D._CACHE.clear()


#: rubrics 4.1 verdicts are judged with in the production store (always strict, whatever bench.yaml says)
PRODUCTION_41_RUBRICS = {"nsfw_craft", "rp_deep", "erp_session", "willingness"}


def _rubrics_41() -> set[str]:
    """Every rubric a 4.1 (bench) case uses: base cases and every ladder set, plus the production store's."""
    from crucibleforge import ladder, profiles
    prof = profiles.load_profile("bench")
    out = {c.get("rubric") for c in profiles._select(prof, prof.get("cases") or {}, False)}
    for key, sets in ladder.discover(prof).items():
        for s in sets:
            out |= {c.get("rubric") for c in ladder.set_cases(prof, key, s["n"])}
    return (out - {None}) | PRODUCTION_41_RUBRICS


@pytest.mark.live_ladder
def test_the_baseline_texts_are_the_legacy_table_except_dated_deliberate_changes():
    """Policy, not a frozen equality: (a) a "|41|" key of a rubric 4.1 uses always equals the table (the
    4.1 guard); (b) any other key may differ only when D.RUBRIC_CHANGES names it with a date, a reason and
    today's sha, so an accidental rubric edit still fails. A rubric added later has no legacy entry."""
    now = D.legacy_table()
    used41 = _rubrics_41()
    assert PRODUCTION_41_RUBRICS <= used41
    for k, was in D.LEGACY_RUBRIC_SHA.items():
        rubric, std, _ = k.split("|")
        if std == "41" and rubric in used41:
            assert k not in D.RUBRIC_CHANGES, f"{k}: a 4.1 rubric cannot be listed as changed"
            assert now[k] == was, f"{k}: a rubric 4.1 verdicts were judged with changed"
        elif k in D.RUBRIC_CHANGES:
            date, sha, why = D.RUBRIC_CHANGES[k]
            assert date and why and now[k] == sha and sha != was, (k, now[k])
        else:
            assert now[k] == was, f"{k}: rubric changed without an entry in judge_digest.RUBRIC_CHANGES"
    assert set(D.RUBRIC_CHANGES) <= set(D.LEGACY_RUBRIC_SHA)
    # an unchanged rubric's unstamped verdict is current; a deliberately changed one's is re-judged
    assert D.stale_reason(_rp_row(rubric="rp_single", judge={"judge_failed": False, "scores": {"prose": 7}})) is None
    assert D.stale_reason(_rp_row()) == "rubric"


def test_real_person_rows_take_the_element_standard_41_key():
    """The 41/45 in a key is the element standard: a jcard real-person row (bench45, no element_standard)
    reads real_person|41|0, which is why that key is in RUBRIC_CHANGES."""
    row = _rp_row(profile="bench45")
    row.pop("element_standard")
    assert D._key(*D._variant(row)) == "real_person|41|0"


def test_an_unstamped_real_person_verdict_goes_stale_when_the_text_changes(monkeypatch):
    row = _rp_row()
    _tighten_real_person(monkeypatch)
    assert D.stale_reason(row) == "rubric"
    assert judge.verdict_outdated(row) == "rubric"
    assert judge.needs_verdict(row)
    # another rubric's verdict is untouched by the real_person edit
    other = _rp_row(rubric="rp_single", judge={"judge_failed": False, "scores": {"prose": 7}})
    assert D.stale_reason(other) is None


def test_a_stamped_verdict_is_judged_by_its_own_stamp(monkeypatch):
    _tighten_real_person(monkeypatch)
    cur = _rp_row()
    think = bool(D._profile_settings("bench")["thinking"])            # the row's profile judge setting
    cur["judge"] = {**cur["judge"], "instrument": D.instrument(cur, "J", think)}
    assert D.stale_reason(cur) is None and judge.verdict_outdated(cur) is None
    old = _rp_row()
    old["judge"] = {**old["judge"], "instrument": {**D.instrument(old, "J", False), "rubric_sha": "0" * 16}}
    assert D.stale_reason(old) == "rubric"


def test_failed_empty_and_checks_only_verdicts_are_never_stale(monkeypatch):
    _tighten_real_person(monkeypatch)
    for j in ({"judge_failed": True, "judge_raw": ""}, {"empty_generation": True},
              {"programmatic": True, "elements": {"results": [], "unjudged": []}}):
        assert D.stale_reason(_rp_row(judge=j)) is None
    assert D.stale_reason(_rp_row(judge=None)) is None


def test_the_board_does_not_use_a_stale_verdict_and_says_judge_pending(monkeypatch):
    _tighten_real_person(monkeypatch)
    rows = [_rp_row(), _rp_row(case_id="JC-RP-2", rubric="rp_single",
                               judge={"judge_failed": False, "scores": {"prose": 7}})]
    out = D.drop_stale(rows)
    assert "judge" not in out[0] and out[0]["judge_pending"] == "rubric" and "judge_model" not in out[0]
    assert out[1] is rows[1]                                         # a current verdict is kept as is
    assert "judge" in rows[0]                                        # the stored row is not touched
    notes = report._coverage(out, {}, None)["notes"]
    assert any("judge pending" in n and "older rubric" in n for n in notes), notes
    assert not any("unjudged" in n for n in notes), notes            # not counted twice


def test_board_filter_takes_stale_verdicts_off(monkeypatch):
    seen = []
    real = D.drop_stale

    def spy(rows):
        seen.append(len(rows))
        return real(rows)
    monkeypatch.setattr(D, "drop_stale", spy)
    report.board_filter("nobody", None, rows=[])
    assert seen == [0]


def test_a_new_verdict_carries_its_instrument(tmp_path, monkeypatch):
    """run_judge stamps rubric, rubric_sha, judge model and thinking on the verdict it writes."""
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    row = _rp_row(rubric="rp_single", judge=None)
    row.pop("judge"); row.pop("judge_model"); row.pop("judge_id")
    (tmp_path / "transcripts_m.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    class Prov:
        type, name = "openai", "fake"

    class JC:
        def __init__(self, inst=None):
            from crucibleforge.instances import from_cand
            self.instance = from_cand({"model_id": "J"})
            self.model_id, self.serve_id = "J", self.instance.sid
            self.concurrency, self.thinking, self.provider, self.label = 1, False, Prov(), "J"
    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: JC())
    monkeypatch.setattr(judge, "run_canary", lambda jc, **k: None)
    monkeypatch.setattr(judge, "check_instance_parity", lambda jc: None)
    monkeypatch.setattr(judge, "_private_case_ids", lambda cfg: set())
    monkeypatch.setattr(judge, "judge_row", lambda jc, r, samples=1: {
        "judge_failed": False, "judge_raw": "{}", "refused": False, "scores": {"prose": 7}})
    cfg = {"models": [{"name": "m", "model_id": "m", "provider": "fake"}], "judge": {"samples": 1},
           "providers": {}, "defaults": {}}
    judge.run_judge(cfg, ["m"])
    got = config.load_transcripts("m")[-1]["judge"]
    assert got["instrument"] == {"rubric": "rp_single", "rubric_sha": D.rubric_digest(row),
                                 "judge_model": "J", "thinking": False}


# ------------------------------------------------ follow-ups (lead 00:23): thinking, and the 4.5 release switch


def _settings(monkeypatch, **by_profile):
    real = D._profile_settings

    def fake(profile):
        return {**real(profile), **by_profile.get(profile, {})}
    monkeypatch.setattr(D, "_profile_settings", fake)


def _stamped(thinking, policy_thinking=None, **over):
    row = _rp_row(profile="bench45", **over)
    pol = {} if policy_thinking is None else {"thinking": policy_thinking}
    row["judge"] = {**row["judge"], "policy": pol, "instrument": D.instrument(row, "J", thinking)}
    return row


def test_a_stamped_verdict_judged_with_another_thinking_setting_is_stale(monkeypatch):
    _settings(monkeypatch, bench45={"thinking": True, "is45": True})   # a 4.5 profile (chains): the rule acts
    assert D.stale_reason(_stamped(True)) is None
    off = _stamped(False)
    assert D.stale_reason(off) == "thinking" and judge.verdict_outdated(off) == "thinking"
    # a rubric whose policy turns thinking off for the call (flag-only) was right to judge without it
    assert D.stale_reason(_stamped(False, policy_thinking=False)) is None
    assert D.stale_reason(_stamped(True, policy_thinking=False)) == "thinking"
    _settings(monkeypatch, bench45={"thinking": False, "is45": True})
    assert D.stale_reason(_stamped(True)) == "thinking" and D.stale_reason(_stamped(False)) is None


def test_the_profile_judge_thinking_is_read_from_the_profile():
    assert D._profile_settings("bench45")["thinking"] is True          # bench45.yaml judge: thinking: true


def test_the_release_switch_makes_an_unstamped_verdict_stale(monkeypatch):
    # rp_single: a rubric unchanged since the baseline (real_person's own text change is tested above)
    row = _rp_row(profile="bench45", rubric="rp_single", judge={"judge_failed": False, "scores": {"prose": 7}})
    assert D.stale_reason(row) is None                                  # off by default
    assert D._profile_settings("bench45")["stamp_required"] is False
    _settings(monkeypatch, bench45={"stamp_required": True})
    assert D.stale_reason(row) == "unstamped" and judge.needs_verdict(row)
    _settings(monkeypatch, bench45={"stamp_required": True, "thinking": True})
    assert D.stale_reason(_stamped(True)) is None                       # a stamped, current verdict stays


def test_a_composition_can_set_the_release_switch(monkeypatch):
    from crucibleforge import profiles
    prof = {"name": "p", "cases": {"x": ["a"]}, "compositions": {"standard": {"judge_stamp_required": True}}}
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "standard")
    assert profiles.apply_composition(dict(prof), "p")["judge_stamp_required"] is True
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "default")
    assert not profiles.apply_composition(dict(prof), "p").get("judge_stamp_required")


def _same(a, b) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(float(a) - float(b)) <= 1e-9)


@pytest.mark.archive_default
def test_the_4_1_golden_board_is_unchanged_with_the_switch_on_for_4_5(tmp_path, monkeypatch):
    """bench (4.1) never has the switch: with it ON for bench45 the golden 4.1 board is exactly
    expected.json. Forcing it on bench as well WOULD change it (the guard can see the effect)."""
    from pathlib import Path
    fix = Path(__file__).parent / "fixtures" / "board41"
    rows = json.loads((fix / "rows.json").read_text(encoding="utf-8"))
    for label, rs in rows.items():
        (tmp_path / f"transcripts_{label}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rs),
                                                             encoding="utf-8")
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    want = json.loads((fix / "expected.json").read_text(encoding="utf-8"))["board"]
    assert any(D._judged(r.get("judge")) and r.get("rubric") for rs in rows.values() for r in rs)
    # the guard holds 4.1-rubric verdicts (rp_deep, willingness), and none of them reads stale
    judged41 = [r for rs in rows.values() for r in rs if D._judged(r.get("judge"))
                and r.get("rubric") in PRODUCTION_41_RUBRICS]
    assert {r["rubric"] for r in judged41} >= {"rp_deep", "willingness"}
    assert all(D.stale_reason(r) is None for r in judged41)

    def board():
        labels, stats, _ = report.board_stats(None, None)
        return {l: (stats[l]["scorecard"].get("total"), stats[l]["scorecard"].get("parts")) for l in labels}
    _settings(monkeypatch, bench45={"stamp_required": True, "thinking": True})
    got = board()
    assert sorted(got) == sorted(want)
    for l, (total, parts) in got.items():
        assert _same(total, want[l]["total"]), l
        for score, ps in want[l]["parts"].items():
            for part, v in ps.items():
                assert _same((parts or {}).get(score, {}).get(part), v), (l, score, part)
    _settings(monkeypatch, bench45={"stamp_required": True}, bench={"stamp_required": True})
    assert board() != got
