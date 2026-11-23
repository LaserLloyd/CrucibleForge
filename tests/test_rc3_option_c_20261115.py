"""rc3-main Coding under option C (lead 2026-11-14 night; G2 EXP-20261115-39 CROSSCHECK-41-45).

Coding = builds what was meant 5 + uses tools 2.5 (the three 4.1 tool-use cases TZ16/TZ19/TZ20, two repeats
each, byte-identical: D2's screen-tools-d2 rows count) + traces 1.25 + predicts 1.25. Trace and Predict keep
every cell and instance; only their points change."""
from __future__ import annotations

import pytest

from crucibleforge import chains, profiles, report, version

TZ = ["TZ16-pickup-dst-delay", "TZ19-hotel-cot-deadline", "TZ20-swim-saturday-age-day"]


def _load(monkeypatch, comp):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, comp)
    return profiles.load_profile("bench45")


def test_option_c_weights_sum_to_ten_and_mental_execution_is_a_quarter(monkeypatch):
    rc3 = _load(monkeypatch, "rc3-main")
    parts = rc3["scores"]["work"]["parts"]
    std = {k: p["max"] for k, p in parts.items() if p.get("max")}
    assert std == {"c1_implied": 5, "c5_tools": 2.5, "c2_trace": 1.25, "c2_predict": 1.25}
    assert sum(std.values()) == 10
    assert parts["c2_trace"]["max"] + parts["c2_predict"]["max"] == 2.5


def test_tool_use_part_runs_the_three_41_cases_twice_with_a_margin_and_ran_out_exclude(monkeypatch):
    rc3 = _load(monkeypatch, "rc3-main")
    tools = rc3["scores"]["work"]["parts"]["c5_tools"]
    assert rc3["cases"]["tooluse"] == TZ and tools["cases"] == TZ and tools["scoring"] == ["tooluse"]
    assert {c["id"]: c["repeats"] for c in profiles._select(rc3, {"tooluse": TZ})} == dict.fromkeys(TZ, 2)
    assert tools["ran_out"] == "exclude" and report.case_part_ran_out(rc3, tools) == "exclude"
    assert 0 < tools["question_sd"] <= 0.1
    from crucibleforge import headline
    assert headline.strata(rc3, report.scoring_config(None), None)[(1, "work/c5_tools")] == sorted(TZ)


def test_d2_screen_rows_count_same_cases_same_repeats_same_stamps(monkeypatch):
    """D2's measure-only screen (screen-tools-d2: TZ16/19/20 x2 under bench45) is the run that feeds this part:
    the case ids, repeats and per-case stamps are the same under rc3-main, so its rows are current here."""
    rc3 = _load(monkeypatch, "rc3-main")
    stamps_rc3 = {c: version.case_hash(c) for c in TZ}
    sel = {c["id"]: c for c in profiles._select(rc3, {"tooluse": TZ})}
    assert all(stamps_rc3.values()) and set(sel) == set(TZ)
    # the stamp is the case's content hash, independent of the composition that runs it
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc2-main")
    profiles.load_profile("bench45")
    assert {c: version.case_hash(c) for c in TZ} == stamps_rc3


def test_trace_and_predict_keep_every_cell_and_item(monkeypatch):
    rc3 = _load(monkeypatch, "rc3-main")
    names = {fs["name"] for s in chains.sections(rc3) for fs in (s.get("flat") or [])}
    for cell in ("rc2_trace4", "rc2_trace6", "rc2_trace8", "rc2_implied1"):
        assert any(n == cell or n.startswith(cell + "_") for n in names), cell
    assert rc3["scores"]["work"]["parts"]["c2_predict"]["cases"] == [
        "CB-C5-predict-pyhouse", "CB-C6-predict-node", "CB-C3-predict-sqlite"]


# ------------------------------------------------------------------ the Claude Code route has no tools
def test_route_cannot_run_names_only_tool_parts_on_claude_code_rows():
    from crucibleforge import report
    tools = {"key": "c5_tools", "scoring": ["tooluse"], "cases": TZ}
    cc = {"provider_type": "claude_code", "provider": "claudecode"}
    assert report.route_cannot_run(cc, tools)
    assert report.route_cannot_run({"provider_type": "openai"}, tools) is None
    assert report.route_cannot_run(cc, {"key": "c2_predict", "scoring": ["codebank"]}) is None


@pytest.mark.live_ladder
@pytest.mark.parametrize("predict_cut", [False, True])
def test_claude_code_row_reads_tools_not_run_and_coding_is_not_scaled(tmp_path, monkeypatch, predict_cut):
    """Lead 2026-11-14 night: the CC validation rows cannot run tool use; that part reads "not run" (never a 0,
    never a dash for missing rows) and nothing is scaled up over it: Coding prints "x of 7.5 measured, 2.5 not run"."""
    import json
    import re
    from pathlib import Path
    from crucibleforge import cli, config
    fix = Path(__file__).with_name("fixtures") / "ds8b_rc2_coding_rows.json"
    rows = json.loads(fix.read_text(encoding="utf-8"))
    label = "cc-x"
    for r in rows:
        r["model_label"] = label
    # rc3's Predict also runs CB-C6 x2 and CB-C3 x1 (the 8B fixture is rc2's: CB-C5 only): clone a finished
    # CB-C5 row under their ids and stamps so Coding is complete apart from the tool part
    src = next(r for r in rows if r["case_id"] == "CB-C5-predict-pyhouse" and r["finish_reason"] == "stop")
    if not predict_cut:
        # every Predict row finished: the only gap is the part the route cannot run
        rows = [r if r["case_id"] != "CB-C5-predict-pyhouse" else {**src, "repeat": r["repeat"]} for r in rows]
    for cid, n in (("CB-C6-predict-node", 2), ("CB-C3-predict-sqlite", 1)):
        for k in range(1, n + 1):
            rows.append({**src, "case_id": cid, "repeat": k, "case_revision": version.case_hash(cid),
                         "bank_revision": version.case_hash(cid)})
    # the 8B's Implied attempts were graded before the 4.5 item-audit Implied key fix moved that family's chain
    # revision: regrade the copies under today's grader (replies untouched) so they are current again
    from crucibleforge import chains, regrade
    from crucibleforge.gen import _det
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc3-main")
    # this test pins the plain D10 rule; the shipped profile says ``cut_at_full_budget: zero`` since 2026-11-19
    _real_load = profiles.load_profile
    monkeypatch.setattr(profiles, "load_profile", lambda *a, **k: {**_real_load(*a, **k), "cut_at_full_budget": "omit"})
    p45 = profiles.load_profile("bench45")
    profiles.activate_cases_root(p45)
    specs = {c["id"]: c for c in chains.chain_cases(p45)}
    recs = regrade.plan(rows, specs, "test: today's grader", None, {}, "work/c1_implied",
                        now="2026-11-18T00:00:00+00:00", commit="test", pin=_det.pinned_python(p45))
    rows = rows + [r for rec in recs if rec["status"] == "regrade" for r in rec["rows"]]
    res = tmp_path / "results"
    res.mkdir()
    (res / f"transcripts_{label}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (res / f"meta_{label}.json").write_text(json.dumps({"model_label": label, "provider": "claudecode",
                                                        "provider_type": "claude_code", "profile": "bench45"}), encoding="utf-8")
    cfgf = tmp_path / "models.yaml"
    cfgf.write_text(
        "providers:\n  claudecode:\n    type: claude_code\n"
        f"models:\n- name: {label}\n  model_id: claude-x\n  provider: claudecode\n  context_length: 131072\n"
        "judge:\n  candidates:\n  - provider: claudecode\n    model_id: j\n"
        "    context_length: 16384\ndefaults:\n  context_length: 131072\n", encoding="utf-8")
    monkeypatch.setenv("CRUCIBLEFORGE_RESULTS", str(res))
    monkeypatch.setattr(config, "RESULTS_DIR", res)
    monkeypatch.setenv("CRUCIBLEFORGE_CONFIG", str(cfgf))
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc3-main")
    try:
        rc = cli.main(["--results", str(res), "report", "--profile", "bench45", "--staged-claude-code"])
    except SystemExit as e:
        rc = e.code
    assert rc in (0, None)
    md = (res / "report.md").read_text(encoding="utf-8")
    # 4.5.0 labels (fix round 2026-11-18, R5): Implied = "Functions", Trace = "Steps", Predict = "Traps"
    line = next(l for l in md.splitlines() if l.startswith(f"| {label} | ") and "Functions" in l)
    assert re.search(r"Tool rules not run \(the Claude Code route has no tools\)", line), line   # R5 label
    # nothing is scaled up over a part that was not run (lead 2026-11-14 night): measured points of the
    # measured maximum, "x of 7.5 measured, 2.5 not run"
    head = line.split(" | ")[1]
    if not predict_cut:
        pts = sum(float(x) for x in re.findall(r"(?:Functions|Steps|Traps) (\d+\.\d)", line))
        assert re.match(r"\d+\.\d of 7\.5 measured, 2\.5 not run =", head + " ="), head
        assert "scaled" not in head, head
        assert abs(float(head.split()[0]) - pts) <= 0.16, (head, pts)
    else:
        # Predict also ran out (the 8B's cut CB-C5 replies): the 4.5 ran-out rule scales over that part
        # only; the not-run part stays out of the maximum
        pts = sum(float(x) for x in re.findall(r"(?:Functions|Steps) (\d+\.\d)", line))
        m = re.match(r"(\d+\.\d) of 7\.5, 2\.5 not run \(scaled from (\d+\.\d) of 6\.25 measured\)", head)
        assert m, head
        assert abs(float(m.group(2)) - pts) <= 0.11, (head, pts)


# --- ran_out on a case part (lead 2026-11-15 00:32, found by the public-output owner): tiers._parts dropped
# the part's own ``ran_out`` key, so c5_tools' ``exclude`` never reached the board and a cut tool-use reply
# counted as a failed one.

def _tool_row(cid, rep, cut=False, ok=True):
    steps = [{"id": f"step{i}", "type": "tool_call", "pass": ok, "detail": ""} for i in (1, 2, 3)]
    return {"model_label": "m", "profile": "bench45", "category": "tooluse", "case_id": cid, "repeat": rep,
            "case_revision": version.case_hash(cid), "turn": None, "difficulty": "hard",
            "finish_reason": "length" if cut else "stop", "truncated": cut,
            "response": "Booked." if not cut else "I will now call", "tool_calls": [],
            "grade": "pass" if ok and not cut else "fail", "elements": {"results": steps if not cut else
                                                                         [dict(s, **{"pass": False}) for s in steps]}}


def test_tiers_parts_carry_a_case_parts_ran_out_rule(monkeypatch):
    from crucibleforge import tiers
    rc3 = _load(monkeypatch, "rc3-main")
    part = next(p for d in tiers.scores(rc3) for p in d["parts"] if p["key"] == "c5_tools")
    assert part.get("ran_out") == "exclude"
    assert report.case_part_ran_out(rc3, part) == "exclude"
    # a part without the key gets none (4.1 parts stay byte-identical)
    assert all("ran_out" not in p for d in tiers.scores(rc3) for p in d["parts"] if p["key"] != "c5_tools")


def _tool_subset(monkeypatch, rows):
    rc3 = _load(monkeypatch, "rc3-main")
    s = report.stats_from_tier_rows("m", None, rc3, {1: rows, 2: [], 3: []}, None, None)
    return s["subset_stats"]["work/c5_tools"]


def test_a_cut_tool_use_unit_is_excluded_not_failed(monkeypatch):
    rows = [_tool_row(c, r) for c in TZ for r in (1, 2)]
    rows[0] = _tool_row(TZ[0], 1, cut=True)
    sub = _tool_subset(monkeypatch, rows)
    ro = sub["ran_out_case"]
    assert ro["rule"] == "exclude" and ro["calls"] == 6 and ro["ran_out"] == 1 and not ro["not_measured"]
    assert not sub["not_measured"]
    # the five finished replies all passed: the cut one is left out, not scored as a fail
    assert sub["tooluse"]["n"] == 5 and sub["tooluse"]["rate"] == 1.0


def test_more_than_a_quarter_cut_reads_not_measured_and_adds_nothing(monkeypatch):
    rows = [_tool_row(c, r, cut=(r == 1)) for c in TZ for r in (1, 2)]   # 3 of 6 cut = 50%
    sub = _tool_subset(monkeypatch, rows)
    assert sub["ran_out_case"]["not_measured"] and sub["not_measured"]
    assert sub["ran_out_case"]["share"] == pytest.approx(0.5)
    assert not (sub.get("tooluse") or {}).get("n")


# --- the same rule on the ladder path (lead 2026-11-15 00:37, PACKS2: a cut pack row scored 0)

def _active45(monkeypatch):
    # the board asks profiles.active_name(); with nothing set it is the 4.1 default, whose case
    # tree (activate_cases_root) has no rc3 packs. The report command sets it; so does this.
    monkeypatch.setattr(profiles, "_ACTIVE", ["bench45"])
    monkeypatch.setattr(report, "_CASES_BY_ID", None)


def _pack(rc3):
    seq = report._ladder_sets(rc3)["work/c2_trace"]
    return next(x for x in seq if x["n"] == 1)


def _pack_rows(ids, cut_n):
    import json
    from crucibleforge import ladder
    spec = json.loads((ladder.LADDER_DIR / "work" / "c2_trace" / "set-1.json").read_text(encoding="utf-8"))
    cby = {c["id"]: c for c in spec["cases"]}
    out = []
    for i, cid in enumerate(ids):
        cut = i < cut_n
        out.append({"model_label": "m", "profile": "bench45", "category": cby[cid]["category"], "case_id": cid,
                    "repeat": 1, "case_revision": version.case_hash(cid), "turn": None,
                    "difficulty": cby[cid].get("difficulty", "hard"),
                    "finish_reason": "length" if cut else "stop", "truncated": cut,
                    "response": "partial" if cut else "ok", "grade": "fail" if cut else "pass",
                    "score": 0.0 if cut else 1.0})
    return out


@pytest.mark.live_ladder
@pytest.mark.parametrize("cut_n,measured", [(1, True), (4, False)])
def test_a_cut_pack_row_follows_the_same_ran_out_rule(monkeypatch, cut_n, measured):
    _active45(monkeypatch)
    rc3 = _load(monkeypatch, "rc3-main")
    pack = _pack(rc3)
    ids = list(pack["cases"])
    assert len(ids) == 12
    st = report.stats_from_tier_rows("m", None, rc3, {1: [], 2: _pack_rows(ids, cut_n), 3: []}, None, None)
    ls = st["ladder_stats"]["work/c2_trace#1"]
    ro = ls["ran_out_case"]
    assert ro["rule"] == "exclude" and ro["calls"] == 12 and ro["ran_out"] == cut_n
    assert ls["not_measured"] is (not measured)          # 1/12 cut is under the limit, 4/12 is over it
    full = report.stats_from_tier_rows("m", None, rc3, {1: [], 2: _pack_rows(ids, 0), 3: []}, None, None)
    cat = cut = None
    for k, v in full["ladder_stats"]["work/c2_trace#1"].items():
        if isinstance(v, dict) and v.get("n") == 12:
            cat = k
    assert cat, "the pack's category block"
    cut = ls.get(cat) or {}
    if measured:
        # the cut reply leaves the mean: the eleven finished replies score as the twelve did
        assert cut.get("n") == 11 and cut.get("rate") == full["ladder_stats"]["work/c2_trace#1"][cat]["rate"]
    else:
        assert not cut.get("n")


@pytest.mark.live_ladder
def test_a_not_measured_pack_adds_nothing_and_says_so(monkeypatch):
    _active45(monkeypatch)
    rc3 = _load(monkeypatch, "rc3-main")

    def fake(src, w, partial=frozenset()):
        out = {"chat": None, "coding": None, "total": None, "tok_per_s": None, "components": {}, "weights": dict(w)}
        out.update({g: 95.0 for g in w})
        return out
    monkeypatch.setattr(report, "scorecard", fake)
    sc = report._declared_weights(rc3, None, None)[0]
    ro = {"rule": "exclude", "calls": 12, "ran_out": 4, "share": 4 / 12, "not_measured": True}
    # c2_trace is a chain part: its standard share comes from its chains (here .95, so its pack opens)
    s = {"chain_stats": {"work/c2_trace": {"complete": True, "share": 0.95, "mean": 0.95}},
         "ladder_stats": {"work/c2_trace#1": {"ran_out_case": ro, "not_measured": True}}}
    card = report.points_scorecard(s, sc, rc3)
    det = card["ladder"]["work"]["c2_trace"]
    s1 = next(x for x in det["sets"] if x["n"] == 1)
    assert s1["state"] == "errored" and s1["pending_reason"] == "not measured" and s1["share"] is None
    assert det["points"] == pytest.approx(det["max"] * 0.95)        # the standard share only: the pack adds 0


# --- the tool-use unit score (lead 2026-11-15 02:22, G2): a run scores half for completing the task with
# every step right and half for its share of right steps. A wrong booking kept most of its credit under the
# step share alone; pass/fail alone is too noisy on six runs. c5_tools only; 4.1 tooluse is untouched.

def _steps_row(cid, rep, n_ok, n):
    steps = [{"id": f"step{i}", "type": "tool_call", "pass": i <= n_ok, "detail": ""} for i in range(1, n + 1)]
    return {"model_label": "m", "profile": "bench45", "category": "tooluse", "case_id": cid, "repeat": rep,
            "case_revision": version.case_hash(cid), "turn": None, "difficulty": "hard", "finish_reason": "stop",
            "truncated": False, "response": "ok", "tool_calls": [],
            "grade": "pass" if n_ok == n else "fail", "elements": {"results": steps}}


def test_tool_use_part_declares_the_half_pass_unit_score_and_its_note(monkeypatch):
    # the tool revision (lead 2026-11-15 08:27) moved the part from half_pass to half_task: the same
    # half-and-half score, with the pass half read as "task done right" (tests/test_tool_revision_20261115.py)
    from crucibleforge import tiers
    rc3 = _load(monkeypatch, "rc3-main")
    part = next(p for d in tiers.scores(rc3) for p in d["parts"] if p["key"] == "c5_tools")
    assert part.get("unit_score") == "half_task"
    assert "half" in rc3["scores"]["work"]["parts"]["c5_tools"]["note"]


def test_a_tool_use_run_scores_half_pass_and_half_step_share(monkeypatch):
    # the reference's six stored runs: 8/8, 8/8, 6/6, 4/6, 11/11, 10/11 -> step share .929, pass 4/6
    rows = [_steps_row(TZ[2], 1, 8, 8), _steps_row(TZ[2], 2, 8, 8), _steps_row(TZ[0], 1, 6, 6),
            _steps_row(TZ[0], 2, 4, 6), _steps_row(TZ[1], 1, 11, 11), _steps_row(TZ[1], 2, 10, 11)]
    sub = _tool_subset(monkeypatch, rows)
    step = (1 + 1 + 1 + 4 / 6 + 1 + 10 / 11) / 6
    assert sub["tooluse"]["element_rate"] == pytest.approx(0.5 * (4 / 6) + 0.5 * step)   # .798, not .929
    # MiniMax / Grok shape: no run fully right -> half the step share
    rows = [_steps_row(c, r, 2, 3) for c in TZ for r in (1, 2)]
    assert _tool_subset(monkeypatch, rows)["tooluse"]["element_rate"] == pytest.approx(0.5 * 2 / 3)


def test_the_unit_score_is_refused_on_a_part_that_is_not_tool_use(monkeypatch):
    from crucibleforge.config import ConfigError
    with pytest.raises(ConfigError):
        report.part_unit_score({"key": "x", "scoring": ["coding"], "unit_score": "half_pass"})
    assert report.part_unit_score({"key": "x", "scoring": ["tooluse"]}) is None
