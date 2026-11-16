"""Regrade in place after a grading-only change (2026-11-14, Admin D's spec, the lead's rulings).

Fixtures are real stored rows (tests/fixtures/regrade_20261114.json, 4.5 calibration runs of
2026-11-14). An attempt whose instance and sent text are byte-identical to what the current
tree builds is graded again by the live run's code and written as superseding rows (same
dedupe key, new chain_revision, ``regraded_by``); anything else is refused and left stale.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from crucibleforge import chains, config, profiles, regrade, session_checks

FX = json.loads((Path(__file__).parent / "fixtures" / "regrade_20261114.json").read_text(encoding="utf-8"))
NOW = "2026-11-14T03:00:00+00:00"
_REAL_DIGEST = chains._family_source_digest


def _setup(monkeypatch, comp):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, comp)
    monkeypatch.delenv("CRUCIBLEFORGE_RELEASE_SEED", raising=False)
    cfg = {"defaults": {}}
    prof = profiles.load_profile("bench45", cfg)
    cfg = profiles.apply_profile(prof, cfg)[0]
    rules = {"empty_stop_rule": bool(cfg["defaults"].get("empty_stop_rule"))}
    return {c["id"]: c for c in chains.chain_cases(prof)}, rules


@pytest.fixture(autouse=True)
def _reset_switches():
    session_checks.set_tag_lead(False)
    session_checks.clear_memo()
    yield
    session_checks.set_tag_lead(False)
    session_checks.clear_memo()


def _fixture(monkeypatch, name, stale=True):
    """A fixture's rows, specs and rules, independent of the live family sources: every row is
    first stamped current under THIS tree (its revision is what this tree computes for its own
    block), then, with ``stale``, its family's source digest is moved (``_stale``), so the rows
    read as made before a grading-only change whatever the integration tree's sources are."""
    fx = FX[name]
    specs, rules = _setup(monkeypatch, fx["composition"])
    rows = copy.deepcopy(fx["rows"])
    monkeypatch.setattr(chains, "_family_source_digest", _REAL_DIGEST)   # undo an earlier _stale
    for r in rows:
        if r["chain"]["id"] not in specs:
            specs[r["chain"]["id"]] = _spec_from_row(r)
        # the ceiling the tree's own profile line gives this rung (a profile ceiling change is a
        # refusal the tool owes, tested on its own; it must not leak into the other fixtures)
        ch = r["chain"]
        inst = chains.instance(specs[ch["id"]], int(ch["rung"]), int(ch["attempt"]),
                               start=int(ch.get("start_rung") or 1))
        ch["ceiling"] = inst["chain"].get("ceiling")
        r["chain_revision"] = chains.revision(r["chain"])
    if stale:
        for fam in sorted({r["chain"]["family"] for r in rows}):
            _stale(monkeypatch, fam)
    return rows, specs, rules


def _plan(monkeypatch, name, stale=True, **kw):
    rows, specs, rules = _fixture(monkeypatch, name, stale)
    return rows, regrade.plan(rows, specs, kw.pop("reason", "test"), rules=rules, now=NOW, commit="abc1234", **kw)


def _score_now(grp, specs, rules):
    """The score this tree's own grader gives one stored attempt's replies, through the run's
    path (``chains.stamp_verdict`` over the live case). Expected scores are computed, never
    pinned: a grader fix that reaches integration moves them, and the tool must follow."""
    ch = grp[-1]["chain"]
    case = chains.materialize(chains.instance(specs[ch["id"]], ch["rung"], ch["attempt"],
                                              start=ch.get("start_rung") or 1))
    return regrade._regrade_rows(copy.deepcopy(grp), case, rules)[-1]["chain"]["score"]


def _spec_from_row(row):
    """The unit spec a profile would give, read back from a stored row's chain block (for a
    unit whose composition this tree no longer carries: pack-unknown's netsurgeon set)."""
    b = row["chain"]
    rung = {"family": b["family"], "level": b["level"], "pass": b["pass_at"], "ceiling": b.get("ceiling"),
            "category": row["category"]}
    for k in ("variant", "concept", "band", "ran_out", "exclude"):
        if b.get(k) is not None:
            rung[k] = b[k]
    sp = {"id": b["id"], "kind": b["kind"], "part": b["part"], "ladder": b["ladder"], "c": b["c"],
          "K": b["K"], "slip_guard": b["slip_guard"], "start": {"policy": "first"}, "rungs": [rung]}
    if b.get("element"):
        sp["element"] = b["element"]
    return {"id": b["id"], "category": row["category"], "chain_spec": sp}


def _stale(monkeypatch, family):
    """An edit to ``family``'s source that changes no prompt (an encoding-only pin bump):
    its rows' stamps move, nothing they were sent does."""
    real = chains._family_source_digest

    def fake(f):
        return "0" * 12 if f == family else real(f)
    monkeypatch.setattr(chains, "_family_source_digest", fake)


# ------------------------------------------------------------ identical prompts: regraded


def test_pytrap_rows_made_before_the_fairness_commit_are_regraded(monkeypatch):
    rows, recs = _plan(monkeypatch, "pytrap_stale", reason="C2 fairness: weight 0 on one line, indented answers")
    assert [r["status"] for r in recs] == ["regrade", "regrade"]
    for rec in recs:
        new = rec["rows"][-1]
        assert new["chain_revision"] == chains.revision(new["chain"]) != rec["from_revision"]
        rb = new["regraded_by"]
        assert rb["reason"].startswith("C2 fairness") and rb["from_revision"] == rec["from_revision"]
        assert rb["commit"] == "abc1234" and rb["regraded"] and rb["carried"] == []
        assert new["ts"] == NOW
        # nothing about the run itself changes
        old = next(r for r in rows if r["case_id"] == new["case_id"])
        for k in ("model_id", "sampler", "protocol", "max_tokens_sent", "temperature", "top_p", "thinking",
                  "response", "reasoning", "cost_usd", "metrics", "bench_run_id", "repeat", "turn", "prompt"):
            assert new.get(k) == old.get(k), k


def test_implied_rows_across_a_grading_only_change(monkeypatch):
    rows, recs = _plan(monkeypatch, "implied_stale")
    assert {r["status"] for r in recs} == {"regrade"}
    by = {r["unit"]: r for r in recs}
    specs, rules = _setup(monkeypatch, FX["implied_stale"]["composition"])
    unit = "FLAT-implied1x3_L1-001"
    grp = next(g for g in regrade.attempts(copy.deepcopy(rows)) if g[-1]["chain"]["id"] == unit)
    assert by[unit]["score_before"] == 0.913                       # what the run stored
    assert by[unit]["score_after"] == _score_now(grp, specs, rules)  # what this tree's grader gives
    s = {x["set"]: x for x in regrade.summary(recs)}
    assert s["implied1x3_L1"]["regrade"] == 1 and s["implied1x3_L1"]["score_after"] == by[unit]["score_after"]


def test_a_regrade_of_a_current_attempt_reproduces_its_stored_verdict(monkeypatch):
    """The regrade uses the live run's code: on rows nothing changed for, it gives the stored
    verdict back exactly (single row, session, check elements)."""
    for name in ("pytrap_stale", "session", "netsurgeon"):
        fx = FX[name]
        specs, rules = _setup(monkeypatch, fx["composition"])
        for grp in regrade.attempts(copy.deepcopy(fx["rows"])):
            ch = grp[-1]["chain"]
            sp = specs.get(ch["id"]) or _spec_from_row(grp[-1])
            case = chains.materialize(chains.instance(sp, ch["rung"], ch["attempt"],
                                                      start=ch.get("start_rung") or 1))
            new = regrade._regrade_rows(grp, case, rules)[-1]
            assert (new["chain"]["score"], new["chain"]["passed"]) == (ch["score"], ch["passed"]), (name, ch["id"])
            assert new["chain"].get("elements") == ch.get("elements"), (name, ch["id"])
            for k in ("grade", "score", "elements", "checks"):
                assert new.get(k) == grp[-1].get(k), (name, k)


def test_netsurgeon_rows_across_an_encoding_only_pin_bump(monkeypatch):
    _rows, recs = _plan(monkeypatch, "netsurgeon", stale=False)
    assert [r["status"] for r in recs] == ["current"]
    _rows, recs = _plan(monkeypatch, "netsurgeon", reason="netsurgeon pin bump (encoding only)")
    assert [r["status"] for r in recs] == ["regrade"]
    assert recs[0]["score_after"] == recs[0]["score_before"]
    assert recs[0]["to_revision"] != recs[0]["from_revision"]


def test_a_session_row_under_speaker_tag_lead(monkeypatch):
    _stale(monkeypatch, "session_state")
    fx = FX["session"]
    specs, rules = _setup(monkeypatch, fx["composition"])
    session_checks.set_tag_lead(True)
    recs = regrade.plan(copy.deepcopy(fx["rows"]), specs, "speaker tags after a quote or bullet",
                        switch="speaker_tag_lead", rules=rules, now=NOW)
    assert [r["status"] for r in recs] == ["regrade"]
    new_rows = recs[0]["rows"]
    assert len(new_rows) == len(fx["rows"])                    # every turn row superseded, one for one
    assert all(r["regraded_by"]["switch"] == "speaker_tag_lead" for r in new_rows)
    assert [r["turn"] for r in new_rows] == sorted(r["turn"] for r in fx["rows"])
    assert recs[0]["score_after"] == recs[0]["score_before"]   # these replies carry no lead before a tag


def test_a_session_attempt_whose_sent_turns_changed_is_refused_under_the_switch(monkeypatch):
    """The same run's L10-001 attempt: aa1cb09 names a present partner where a line went to the departed one, so what was sent
    differs from what this tree builds; the switch never makes it a regrade."""
    _stale(monkeypatch, "session_state")
    fx = FX["session_sent_changed"]
    specs, rules = _setup(monkeypatch, fx["composition"])
    session_checks.set_tag_lead(True)
    recs = regrade.plan(copy.deepcopy(fx["rows"]), specs, "speaker tags after a quote or bullet",
                        switch="speaker_tag_lead", rules=rules, now=NOW)
    assert [r["status"] for r in recs] == ["refused"] and "rows" not in recs[0]
    assert recs[0]["why"].startswith("what was sent differs: turn ") and "user text differs" in recs[0]["why"]


# ------------------------------------------------------------ refusals: left stale


def test_a_changed_prompt_is_refused_with_the_first_difference(monkeypatch):
    _rows, recs = _plan(monkeypatch, "implied_prompt_changed")
    assert [r["status"] for r in recs] == ["refused"]
    assert "prompt differs at char 1003" in recs[0]["why"] and "rows" not in recs[0]


def test_a_mutated_session_turn_is_refused(monkeypatch):
    _stale(monkeypatch, "session_state")
    fx = FX["session"]
    specs, rules = _setup(monkeypatch, fx["composition"])
    rows = copy.deepcopy(fx["rows"])
    rows[2]["prompt"] = rows[2]["prompt"] + " "
    recs = regrade.plan(rows, specs, "t", rules=rules, now=NOW)
    assert recs[0]["status"] == "refused" and f"turn {rows[2]['turn']} user text differs" in recs[0]["why"]


def _without_ran_out(monkeypatch, name, **kw):
    """The stored rows ran under ``ran_out: exclude``; the profile now leaves it out (the instance
    block differs in exactly that key, whatever the integration profile says today)."""
    rows, specs, rules = _fixture(monkeypatch, name)
    for sp in specs.values():
        for rg in sp["chain_spec"]["rungs"]:
            rg.pop("ran_out", None)
    return rows, regrade.plan(rows, specs, kw.pop("reason", "test"), rules=rules, now=NOW, commit="abc1234", **kw)


def test_a_changed_instance_block_is_refused_unless_it_is_the_named_switch(monkeypatch):
    _rows, recs = _without_ran_out(monkeypatch, "istack_ran_out")
    assert recs[0]["status"] == "refused" and "ran_out 'exclude' -> None" in recs[0]["why"]
    _rows, recs = _without_ran_out(monkeypatch, "istack_ran_out", switch="ran_out")
    assert recs[0]["status"] == "regrade" and recs[0]["rows"][-1]["regraded_by"]["switch"] == "ran_out"
    assert "ran_out" not in recs[0]["rows"][-1]["chain"]


def test_a_new_ceiling_is_never_restamped(monkeypatch):
    rows, specs, rules = _fixture(monkeypatch, "pytrap_stale")
    for sp in specs.values():
        for rg in sp["chain_spec"]["rungs"]:
            rg["ceiling"] = 999
    recs = regrade.plan(rows, specs, "t", rules=rules, now=NOW)
    assert {r["status"] for r in recs} == {"refused"} and all("ceiling" in r["why"] for r in recs)
    with pytest.raises(ValueError):
        regrade.plan([], specs, "t", switch="sampler")


def test_judged_elements_that_changed_go_to_a_re_judge(monkeypatch):
    rows, specs, rules = _fixture(monkeypatch, "pytrap_stale")
    for r in rows:
        r["judge_elements"] = [{"id": "e1", "q": "old wording"}]
    recs = regrade.plan(rows, specs, "t", rules=rules, now=NOW)
    assert {r["status"] for r in recs} == {"refused"} and all("re-judge" in r["why"] for r in recs)


def test_an_errored_attempt_is_refused():
    rows = copy.deepcopy(FX["pytrap_stale"]["rows"][:1])
    rows[0]["chain"]["passed"] = None
    rows[0]["grade"] = "error"
    recs = regrade.plan(rows, {}, "t", now=NOW)
    assert recs[0]["status"] == "refused" and "errored" in recs[0]["why"]


# ------------------------------------------------------------ superseding rows: readers + idempotence


def test_superseding_rows_replace_the_old_for_every_reader_and_a_rerun_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "results_dir", lambda: tmp_path)
    rows, recs = _plan(monkeypatch, "implied_stale")
    label = "m"
    for r in rows:
        config.append_transcript(label, r)
    for rec in recs:
        for r in rec["rows"]:
            config.append_transcript(label, r)
    loaded = config.load_transcripts(label)
    assert len(loaded) == len(rows)                            # one row per key: the superseding one
    assert all(r.get("regraded_by") for r in loaded)
    blocks = chains._newest_blocks(loaded)
    after = {rec["unit"]: rec["score_after"] for rec in recs}
    assert {k[0]: b["score"] for k, b in blocks.items()} == after   # readers see the regraded verdicts
    assert all(chains.usable_row(r) for r in loaded if (r.get("chain") or {}).get("passed") is not None)
    # scale reads the raw file through the same rule
    from crucibleforge import scale
    _lab, srows = scale._load(f"{label}={tmp_path}")
    assert len(srows) == len(rows) and all(r.get("regraded_by") for r in srows)
    # idempotent: the rows are current now
    specs, rules = _setup(monkeypatch, FX["implied_stale"]["composition"])
    again = regrade.plan(loaded, specs, "test", rules=rules, now=NOW)
    assert {r["status"] for r in again} == {"current"}


def test_a_routed_row_keeps_its_route(monkeypatch):
    rows, specs, rules = _fixture(monkeypatch, "pytrap_stale")
    for r in rows:
        r["route"] = {"locator": 0.71, "rung": "mid"}
    recs = regrade.plan(rows, specs, "t", rules=rules, now=NOW)
    assert {r["status"] for r in recs} == {"regrade"}
    assert all(x["route"] == {"locator": 0.71, "rung": "mid"} for rec in recs for x in rec["rows"])


def test_dry_run_render_lists_sets_and_refusals(monkeypatch):
    _rows, recs = _plan(monkeypatch, "implied_prompt_changed")
    out = regrade.render(recs)
    assert "| part | set | identical (regraded) | differing (refused) | current |" in out
    assert "Refused (left stale):" in out and "char 1003" in out


# ------------------------------------------------------------------ --stamp element_standard (lead 2026-11-14)

_STAMP_FX = Path(__file__).parent / "fixtures" / "stamp_theme_w_20261114.json"


def _stamp_case(monkeypatch):
    """A theme_w attempt N2's nsfw-screen-1 run made before the runner stamped element_standard (the
    reply is left out of the fixture: the stamp never reads it)."""
    monkeypatch.delenv("CRUCIBLEFORGE_RELEASE_SEED", raising=False)
    from crucibleforge import gen
    monkeypatch.setattr(gen, "_RELEASE_SEED", ["4.5.0"])
    grp = json.loads(_STAMP_FX.read_text(encoding="utf-8"))
    sp = _spec_from_row(grp[-1])
    return grp, {sp["id"]: sp}


@pytest.mark.live_ladder
def test_stamp_records_element_standard_and_changes_no_grade(monkeypatch):
    grp, specs = _stamp_case(monkeypatch)
    assert "element_standard" not in grp[-1]
    before = json.dumps(grp, sort_keys=True)
    (r,) = regrade.plan_stamp(grp, specs, "element_standard", "4.5 case fact", now="2026-11-14T00:00:00+00:00")
    assert json.dumps(grp, sort_keys=True) == before                 # stored rows untouched
    assert r["status"] == "regrade" and r["stamp"] == {"element_standard": 45}
    new = r["rows"][-1]
    assert new["element_standard"] == 45
    assert new["chain_revision"] == grp[-1]["chain_revision"]        # nothing regraded
    assert new["chain"] == grp[-1]["chain"] and new.get("checks") == grp[-1].get("checks")
    assert new["regraded_by"]["stamp"] == {"element_standard": 45} and new["regraded_by"]["regraded"] == []
    from crucibleforge import judge as J
    assert J.case_field(new, "element_standard") == 45 and J.case_field(grp[-1], "element_standard") is None
    # a second pass is current
    assert regrade.plan_stamp(r["rows"], specs, "element_standard", "again")[0]["status"] == "current"


@pytest.mark.live_ladder
def test_stamp_refuses_a_row_whose_case_cannot_be_shown(monkeypatch):
    grp, specs = _stamp_case(monkeypatch)
    bad = copy.deepcopy(grp)
    bad[-1]["prompt"] = bad[-1]["prompt"] + " "
    assert regrade.plan_stamp(bad, specs, "element_standard", "t")[0]["status"] == "refused"
    nofam = copy.deepcopy(grp)
    nofam[-1]["chain"].pop("family")
    assert "no generator family" in regrade.plan_stamp(nofam, specs, "element_standard", "t")[0]["why"]
    assert "not in this profile" in regrade.plan_stamp(grp, {}, "element_standard", "t")[0]["why"]
    other = copy.deepcopy(grp)
    other[-1]["chain"]["seed_id"] = "0" * 16
    assert "instance differs" in regrade.plan_stamp(other, specs, "element_standard", "t")[0]["why"]
    said41 = copy.deepcopy(grp)
    said41[-1]["element_standard"] = 41
    assert "the rows say" in regrade.plan_stamp(said41, specs, "element_standard", "t")[0]["why"]
    with pytest.raises(ValueError):
        regrade.plan_stamp(grp, specs, "rubric", "t")


@pytest.mark.live_ladder
def test_stamp_is_na_for_a_case_without_the_key(monkeypatch):
    grp, specs = _stamp_case(monkeypatch)
    real = chains.materialize

    def m41(inst):
        c = real(inst)
        c.pop("element_standard", None)
        return c
    monkeypatch.setattr(chains, "materialize", m41)
    assert regrade.plan_stamp(grp, specs, "element_standard", "t")[0]["status"] == "n/a"


# ------------------------------------------------------------ sessions made under an older run rule


def _session(monkeypatch):
    _stale(monkeypatch, "session_state")
    fx = FX["session"]
    specs, rules = _setup(monkeypatch, fx["composition"])
    rows = sorted(copy.deepcopy(fx["rows"]), key=lambda r: r["turn"])
    return rows, specs, rules


def test_a_session_whose_degenerate_turn_was_fed_back_is_refused(monkeypatch):
    # made before f70f9ee: a looping turn went into history as the reply, so every later turn was
    # sent a history the current rule sends empty -> the whole session is refused
    rows, specs, rules = _session(monkeypatch)
    loop = "I will keep going with the same line. " * 300
    rows[1].update(response=loop, finish_reason="stop")
    assert regrade.session_rule_refusal(rows)
    recs = regrade.plan(rows, specs, "t", rules=rules, now=NOW)
    assert recs[0]["status"] == "refused" and "before f70f9ee" in recs[0]["why"]
    assert f"turn {rows[1]['turn']}" in recs[0]["why"]
    # the same turn under the current rule (stamped: it went into history empty) is not this refusal
    rows[1]["degenerate"] = {"channel": "answer", "ratio": 0.9, "chars": len(loop), "history": "empty"}
    assert regrade.session_rule_refusal(rows) is None
    # and a loop on the LAST turn sent nothing later: not this refusal either
    rows2, _s, _r = _session(monkeypatch)
    rows2[-1].update(response=loop, finish_reason="stop")
    assert regrade.session_rule_refusal(rows2) is None


def test_a_session_whose_regenerate_replayed_the_first_attempt_is_refused(monkeypatch):
    # Admin X: before regen-fresh-seed (4a311c2) a regenerate resent the case seed, so a seeded
    # server replayed the empty reply: no retry_seed AND first tokens/reasoning tail == final's
    rows, specs, rules = _session(monkeypatch)
    r = rows[1]
    r.setdefault("metrics", {})["completion_tokens"] = 812
    r["reasoning"] = (r.get("reasoning") or "") + "x" * 80 + " the same thought ends here."
    replay = {"mode": "regenerate", "first_completion_tokens": 812,
              "first_reasoning_chars": len(r["reasoning"]), "first_reasoning_tail": r["reasoning"][-60:],
              "retry_visible": True}
    r["empty_stop_retry"] = dict(replay)
    assert regrade.replayed_regenerate(r)
    recs = regrade.plan(rows, specs, "t", rules=rules, now=NOW)
    assert recs[0]["status"] == "refused" and "replayed the first attempt" in recs[0]["why"]
    assert "4a311c2" in recs[0]["why"]
    # valid and kept: a retry that differed (no retry_seed), or one made after the fix (retry_seed)
    r["empty_stop_retry"] = {**replay, "first_completion_tokens": 640}
    assert not regrade.replayed_regenerate(r) and regrade.session_rule_refusal(rows) is None
    r["empty_stop_retry"] = {**replay, "retry_seed": 123456}
    assert not regrade.replayed_regenerate(r) and regrade.session_rule_refusal(rows) is None
