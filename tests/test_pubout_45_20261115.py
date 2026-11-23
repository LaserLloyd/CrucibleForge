"""PUBOUT (lead, 2026-11-15): the public output of a 4.5 board, the nine items. Display only:
none of these tests may see a score move."""
from __future__ import annotations

import pytest

from crucibleforge import profiles, report, tiers


# ---- item 4: public labels carry no internal tags, never cut mid-word
@pytest.mark.parametrize("raw,want", [
    ("Builds what was meant (C1)", "Builds what was meant"),
    ("Reads and traces code (C2)", "Reads and traces code"),
    ("Predicts what code prints (checked)", "Predicts what code prints"),
    ("Range (role-play", "Range"),                      # unquoted YAML flow value: "(role-play, judged)"
    ("Lockout (intensity rungs)", "Lockout"),
    ("Sounds like a real person (judged) (provisional)", "Sounds like a real person"),
    ("Role-play", "Role-play"),
    ("Keeps a tally", "Keeps a tally"),
])
def test_public_label(raw, want):
    assert tiers.public_label(raw) == want


def test_public_profile_parts_keep_the_internal_label_apart():
    prof = {"chains": {"x": {}}, "scores": {"work": {"label": "Coding", "parts": {
        "c1": {"label": "Builds what was meant (C1)", "max": 5},
        "c2": {"label": "Range (role-play", "max": 2.5}}}}}
    pub = tiers.scores({**prof, "_public": True})[0]["parts"]
    assert [p["label"] for p in pub] == ["Builds what was meant", "Range"]
    assert [p["label_internal"] for p in pub] == ["Builds what was meant (C1)", "Range (role-play"]
    assert [p["key"] for p in pub] == ["c1", "c2"]
    # the operator's board keeps its working labels, weights untouched
    own = tiers.scores(prof)[0]["parts"]
    assert [p["label"] for p in own] == ["Builds what was meant (C1)", "Range (role-play"]
    assert [p["weight"] for p in own] == [p["weight"] for p in pub]


# ---- item 4: no rung id ("LK04") on a public board
def test_lockout_text_names_the_rung_by_place_when_public():
    lab = "locked at LK04 · wrote 9 of 10"
    assert report.lockout_text(lab) == "wrote 9 of 10, first refusal LK04"      # operator board
    tok = report._PUBLIC_WORDS.set(True)
    try:
        assert report.lockout_text(lab) == "wrote 9 of 10, first refusal on rung 4"
        assert report.lockout_text("locked at LK02-heat (3/10)") == "first refusal on rung 2 (3 of 10 written)"
        assert "LK" not in report.lockout_text("odd LK07-x form")
    finally:
        report._PUBLIC_WORDS.reset(tok)


# ---- item 6: --public leaves the Claude Code validation rows out (it used to abort)
def _patch_board(monkeypatch, metas):
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: {})
    monkeypatch.setattr(report, "board_view", lambda label, cfg: ([{"case_id": "x"}], {"old": False}))
    monkeypatch.setattr(report, "load_meta", lambda label: dict(metas[label]))
    monkeypatch.setattr(report, "tier_model_stats",
                        lambda label, cfg, rows, meta, era: {"coverage": {}, "meta": meta, "flags": {}})
    for name in ("_cards", "_headline", "_packs_pending_provisional"):
        monkeypatch.setattr(report, name, lambda *a, **k: None)
    monkeypatch.setattr(report, "row_facts", lambda label, meta, cfg: {})
    monkeypatch.setattr(report, "room_stats", lambda *a, **k: {})
    # the stub rows carry no grader stamps; the stale/undisclosed hold has its own tests (lane-t hooks file)
    monkeypatch.setattr(report, "held45", lambda *a, **k: [])


def test_public_board_leaves_claude_code_rows_out(monkeypatch):
    metas = {"api": {"provider": "deepseek", "provider_type": "openai"},
             "cc-haiku": {"provider": "claudecode", "provider_type": "claude_code"},
             "cc-sig": {"cost_basis": "subscription", "sampling": "harness-default"}}
    _patch_board(monkeypatch, metas)
    labels, stats, dropped = report.board_stats("api,cc-haiku,cc-sig", {"_audience": report.PUBLIC})
    assert labels == ["api"] and set(stats) == {"api"} and dropped == 0
    # the operator's board still refuses them (guard unchanged)
    with pytest.raises(report.ClaudeCodeOnBoard, match="cc-haiku"):
        report.board_stats("api,cc-haiku", None)


# ---- item 2: judge model and its settings on a 4.5 row (judged parts are chain rows)
def test_judges45_names_the_judge_and_its_settings(monkeypatch):
    monkeypatch.setattr(report, "_primary_judge", lambda cfg: "studioforge/gemma-31b")
    st = {"coverage": {"judges": [], "notes": [], "tier": 0}}
    rows = [{"judge_model": "studioforge/gemma-31b", "judge": {"policy": {"thinking": False, "samples": 3}}},
            {"judge_model": "studioforge/gemma-31b", "judge": {"policy": {"thinking": False, "samples": 3}}},
            {"judge_model": "ref", "rubric": "reference"}]
    report._judges45(st, rows, None)
    assert st["coverage"]["judges"] == ["studioforge/gemma-31b"]
    assert not st["coverage"]["judge_mismatch"] and st["coverage"]["tier"] == 0
    assert st["judge_settings"] == {"models": [report.short_model("studioforge/gemma-31b")],
                                    "thinking": "off", "samples": [3], "replies": 2}


# ---- item 5: coverage from the composition's own ids; "partial" said plainly
def test_coverage45_counts_the_composition_ids():
    st = {"coverage": {"notes": ["partial (7/97 cases)"], "tier": 1, "status": "partial (7/97 cases)"}}
    ids = [f"c{i}" for i in range(10)]
    report._coverage45(st, ids, {f"c{i}" for i in range(9)} | {"other"}, [{"case_id": "c8"}])
    cov = st["coverage"]
    assert (cov["cases"], cov["expected_cases"], cov["complete"]) == (8, 10, False)
    assert cov["notes"][0] == "partial (8/10 cases)" and cov["notes"].count("partial (8/10 cases)") == 1
    assert "2 of the 10 test items" in cov["partial_note"]
    st = {"coverage": {"notes": ["partial (7/97 cases)"], "tier": 1}}
    report._coverage45(st, ids, set(ids), [])
    assert st["coverage"]["complete"] and st["coverage"]["status"] == "full (10 cases)"
    assert st["coverage"]["tier"] == 0 and "partial_note" not in st["coverage"]


# ---- item 8: cut replies left out of the score are not "counted failed"
def test_efficiency_cell_says_what_happened_to_cut_replies():
    e = {"rate": 0.9, "n": 30, "ran_out": 3, "escalated": 0, "pending": 3, "excluded": 3}
    txt = report.efficiency_cell({"efficiency": e})
    assert "counted failed" not in txt and "3 left out of the score" in txt
    e = {**e, "excluded": 2}
    assert "1 counted failed" in report.efficiency_cell({"efficiency": e})
    # no exclude rule: the old wording stands (4.1 boards byte-identical)
    old = {"rate": 0.9, "n": 30, "ran_out": 3, "escalated": 0, "pending": 3}
    assert "ran out" in report.efficiency_cell({"efficiency": old})


def test_ran_out_rule_of_reads_the_part_rule(monkeypatch):
    prof = {"chains": {"x": {}}, "scores": {"work": {"parts": {
        "bank": {"label": "Builds", "max": 5, "cases": ["CB-1"], "scoring": ["codebank"]},
        "tools": {"label": "Uses tools", "max": 2.5, "cases": ["TZ16"], "ran_out": "zero"}}}}}
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: prof)
    assert report._ran_out_rule_of([{"case_id": "CB-1"}], None) == "exclude"
    # the wording follows exactly the rule the score used (case_part_ran_out on the tiers part def)
    tz = next(p for d in tiers.scores(prof) for p in d["parts"] if p["key"] == "tools")
    assert report._ran_out_rule_of([{"case_id": "TZ16"}], None) == report.case_part_ran_out(prof, tz)
    assert report._ran_out_rule_of([{"case_id": "nope"}], None) is None
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: {})
    assert report._ran_out_rule_of([{"case_id": "CB-1"}], None) is None        # 4.1: no rule


# ---- items 1 + 9: a 4.5 board's components are its parts, weights read from the profile
def _scorecard(weights):
    parts = [{"key": k, "label": l, "weight": w} for k, l, w in weights]
    return {"scorecard": {"score_defs": [{"key": "work", "in_total": True, "parts": parts},
                                         {"key": "adult", "in_total": False,
                                          "parts": [{"key": "a", "label": "A", "weight": 10}]}],
                          "ladder": {"work": {k: {"std_share": 0.5} for k, _l, _w in weights}}}}


def test_components_are_the_parts_and_follow_the_weights(monkeypatch):
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: {"chains": {"x": {}}})
    w = [("c1", "Builds what was meant", 5), ("c5", "Uses tools", 2.5), ("c2", "Reads and traces code", 1.25),
         ("c3", "Predicts what code prints", 1.25), ("packs", "Bonus packs", 0)]
    s = _scorecard(w)
    comps = report.html_components({"m": s}, None)
    assert [c["label"] for c in comps] == ["Builds what was meant", "Uses tools", "Reads and traces code",
                                           "Predicts what code prints"]
    assert all(c["key"].startswith("work/") for c in comps)
    assert not {"Programs", "Code bank", "Deep RP", "Reason", "Lockout"} & {c["label"] for c in comps}
    assert report.parts_component_values(s) == {l: 50.0 for _k, l, wt in w if wt > 0}
    # a different weighting changes nothing but what the profile says (nothing hard-codes 5/3/2)
    s2 = _scorecard([("c1", "Builds what was meant", 5), ("c2", "Reads and traces code", 3),
                     ("c3", "Predicts what code prints", 2)])
    assert [c["label"] for c in report.html_components({"m": s2}, None)] == [
        "Builds what was meant", "Reads and traces code", "Predicts what code prints"]


def test_legacy_board_keeps_its_components(monkeypatch):
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: {})
    assert not report.parts_board45(None)


# ---- item 7: the private-term guard reads another checkout in place; fails closed on 0
def _private_tree(root):
    (root / "cases" / "private").mkdir(parents=True)
    (root / "profiles").mkdir()
    (root / "cases" / "private" / "zzsecretcat.json").write_text(
        '[{"id": "ZZS-01", "prompt": "x"}, {"id": "ZZS-02", "prompt": "y"}]', encoding="utf-8")
    (root / "profiles" / "bench.private.yaml").write_text(
        "private: [zzsecretcat]\nlabels: {zzsecretcat: Zz Secret Label}\n", encoding="utf-8")


def test_public_scope_reads_a_private_checkout(monkeypatch, tmp_path):
    # the conftest's CRUCIBLEFORGE_NO_PRIVATE=1 keeps this tree's own private files out; an explicit
    # checkout is still read (hiding more is the safe direction)
    _private_tree(tmp_path)
    monkeypatch.delenv(profiles.PRIVATE_FROM_ENV, raising=False)
    base = profiles.public_private_scope(None)
    monkeypatch.setenv(profiles.PRIVATE_FROM_ENV, str(tmp_path))
    sc = profiles.public_private_scope(None)
    assert {"zzsecretcat", "ZZS-01", "ZZS-02", "Zz Secret Label"} <= set(sc["terms"])
    assert set(base["terms"]) <= set(sc["terms"])
    # the judge / planner scope is unchanged by the switch
    assert "ZZS-01" not in profiles.private_scope(None)["case_ids"]
    assert report.check_private_scope(sc) == len(sc["terms"])
    assert report.public_leaks("row ZZS-02 here", sc) == ["ZZS-02"]


def test_public_scope_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(profiles.PRIVATE_FROM_ENV, str(tmp_path / "nowhere"))
    with pytest.raises(profiles.ConfigError):
        profiles.public_private_scope(None)
    (tmp_path / "empty" / "cases").mkdir(parents=True)
    monkeypatch.setenv(profiles.PRIVATE_FROM_ENV, str(tmp_path / "empty"))
    with pytest.raises(report.PrivateLeak, match="0 private terms"):
        report.check_private_scope({"terms": []})


# ---- lead 2026-11-15: one label mechanism (id / tag / note per part), numbers as separate fields
def test_part_id_tag_note_carried_and_public_tag_split():
    prof = {"public_labels": True, "scores": {"work": {"label": "Coding", "parts": {
        "c2": {"label": "Reads and traces code (C2)", "max": 1.25, "id": "C2",
               "note": "scored by prefix: credit up to the first wrong line"},
        "c1": {"label": "Builds what was meant", "max": 5, "tag": "(C1)"}}}}}
    own = {p["key"]: p for p in tiers.scores(prof)[0]["parts"]}
    assert own["c2"]["note"].startswith("scored by prefix") and own["c2"]["id"] == "C2"
    pub = {p["key"]: p for p in tiers.scores({**prof, "_public": True})[0]["parts"]}
    assert pub["c2"]["label"] == "Reads and traces code" and pub["c2"]["tag"] == "(C2)"
    assert pub["c1"]["label"] == "Builds what was meant" and pub["c1"]["tag"] == "(C1)"
    # a 4.1-style part with none of the keys carries none of them (byte-identical 4.1 output)
    plain = tiers.scores({"scores": {"w": {"parts": {"x": {"label": "X (C9)", "max": 1}}}}})[0]["parts"][0]
    assert not {"id", "tag", "note", "label_internal"} & set(plain)
    # and a 4.1 profile (no chains) keeps its public labels as they were (4.1 is frozen)
    old41 = tiers.scores({"_public": True, "scores": {"w": {"parts": {"x": {"label": "X (C9)", "max": 1}}}}})
    assert old41[0]["parts"][0]["label"] == "X (C9)"


def test_points_measured_fields():
    s = {"scorecard": {"score_defs": [{"key": "work", "parts": [
            {"key": "c1", "label": "Builds", "weight": 5}, {"key": "c5", "label": "Uses tools", "weight": 2.5},
            {"key": "c2", "label": "Traces", "weight": 1.25}, {"key": "pk", "label": "Packs", "weight": 0}]}],
                       "subtotals": {"nsfw": {"label": "NSFW", "points": 7.3, "max": 10, "measured_max": 6,
                                              "pending": ["sexting"], "parts_points": {}}}},
         "headline": {"P": {"work": 6.0, "work/c1": 5.0, "work/c5": None, "work/c2": None},
                      "not_measured": {"work/c5": "the Claude Code route has no tools", "work/c2": 0.4}}}
    m = report.points_measured(s)
    w = m["work"]
    assert (w["points"], w["standard_points"], w["max"], w["measured_max"], w["pending_points"]) == (
        6.0, 5.0, 8.75, 5.0, 3.75)
    assert [(p["part"], p["reason"]) for p in w["pending"]] == [
        ("c5", "not run: the Claude Code route has no tools"),
        ("c2", "not measured: 40% of its calls ran out")]
    assert m["subtotals"]["nsfw"] == {"label": "NSFW", "points": 7.3, "max": 10, "measured_max": 6,
                                      "pending": ["sexting"]}


def test_board_note_is_its_own_field_and_a_note():
    cfg = {"models": [{"name": "grok", "provider": "xai", "board_note": "Run at the provider's default\n reasoning effort."},
                      {"name": "plain", "provider": "xai"}], "providers": {"xai": {"type": "openai"}}}
    f = report.row_facts("grok", {"provider_type": "openai"}, cfg)
    assert f["board_note"] == "Run at the provider's default reasoning effort."
    assert "board_note" not in report.row_facts("plain", {"provider_type": "openai"}, cfg)
    assert f["board_note"] in report.notes_cell({"board_note": f["board_note"]})


def test_part_notes_line():
    defs = [{"key": "work", "parts": [{"label": "Reads and traces code", "weight": 1.25,
                                       "note": "scored by prefix: credit up to the first wrong line"},
                                      {"label": "Builds what was meant", "weight": 5}]}]
    assert report.part_notes_line(defs) == ("*Part notes: Reads and traces code: scored by prefix: "
                                            "credit up to the first wrong line.*")
    assert report.part_notes_line([{"parts": [{"label": "x", "weight": 1}]}]) is None


def test_board_data_components_carry_key_max_and_note():
    import importlib
    rh = importlib.import_module("crucibleforge.templates.board.render_html")
    comps = rh._components([], [{"label": "Reads and traces code", "side": "work", "key": "work/c2_trace",
                                          "max": 1.25, "note": "scored by prefix"}, {"label": "Old", "side": "chat"}])
    assert comps == [{"label": "Reads and traces code", "side": "work", "key": "work/c2_trace", "max": 1.25,
                      "note": "scored by prefix"}, {"label": "Old", "side": "chat"}]


def test_an_absent_private_list_says_the_check_was_skipped(monkeypatch, capsys):
    monkeypatch.delenv(profiles.PRIVATE_FROM_ENV, raising=False)
    assert report.check_private_scope({"terms": []}) == 0
    err = capsys.readouterr().err
    assert "private-term check SKIPPED, no list is configured" in err
    assert profiles.PRIVATE_FROM_ENV in err
    assert report.check_private_scope({"terms": ["x"]}) == 1
    assert "SKIPPED" not in capsys.readouterr().err
