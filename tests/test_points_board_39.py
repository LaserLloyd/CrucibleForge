"""Suite 3.9 points board (the maintainer 2026-10-28): categories Coding/Work, Chat,
NSFW out of 10 points each (sub-score points), Overall = Coding/Work + Chat,
ladder packs (+1 point per pack, unlocked at >= 90 % on everything below),
custom tests (Max NSFW RP never in NSFW/Overall, never public), MAXED,
calibration, `pack new`/`pack verify`, and the bootstrap ± / tie groups on
top. Runs as a clean clone (conftest)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from crucibleforge import cli, ladder, profiles, report, tiers, version
from crucibleforge import config as cfgmod
from crucibleforge import headline as h

REPO_PROFILE = Path(__file__).resolve().parent.parent / "profiles" / "bench.yaml"


def _setup(tmp_path, monkeypatch, edit=None, resamples=300):
    """A copy of the live bench profile (``edit`` mutates it), an empty
    ladder dir and a results dir, all under tmp_path."""
    prof = yaml.safe_load(REPO_PROFILE.read_text(encoding="utf-8"))
    # the board mechanics are tested on a FIXED Work set (the step-6 one, WORK
    # below), so swapping questions in the live profile (step 8 re-calibration)
    # does not move every expected number here
    for cat, items in WORK.items():
        if cat != "instruct":
            prof["cases"][cat] = [cid for cid, _ in items]
    # 4.0 restructured the live Work category (Functions / Predict / Tool use)
    # and its repeats; the mechanics here stay pinned on the 3.9 Work block
    prof["scores"]["work"] = copy.deepcopy(WORK_SCORES_39)
    prof["repeats"].update({"coding": 2, "codeagent": 2, "tooluse": 2})
    prof["escalation_on_board"] = True        # the 3.9 M1 rule these tests pin
    prof["escalation_rungs"] = [65536, 131072]   # and its two rungs
    if edit:
        edit(prof)
    pdir = tmp_path / "profiles"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "bench.yaml").write_text(yaml.safe_dump(prof, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(profiles, "_dirs", lambda cfg=None: [pdir])
    lad = tmp_path / "ladder"
    lad.mkdir(exist_ok=True)
    monkeypatch.setattr(ladder, "LADDER_DIR", lad)
    res = tmp_path / "results"
    res.mkdir(exist_ok=True)
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", res)
    monkeypatch.setattr(report, "_PROFILE_LABELS", None)
    monkeypatch.setattr(report, "_CASES_BY_ID", None)
    monkeypatch.setattr(h, "RESAMPLES", resamples)
    cfg = cfgmod.load_config(cfgmod.EXAMPLE_CONFIG_PATH)
    cfgmod.set_results_dir(res)
    return SimpleNamespace(cfg=cfg, results=res, ladder=lad, pdir=pdir)


def _work_only(prof):
    """Overall over Coding/Work alone (the synthetic boards carry no judged
    Chat rows)."""
    prof["scores"]["chat"]["include_in"] = "none"


# The step-6 standard Work set, pinned as this file's fixture (see _setup).
WORK = {"coding": [("CZ05-optimal-bst-cost", 2), ("CZ02-segment-crossings", 2)],
        "codebank": [(c, 1) for c in ("CB-D4-watch-sessions", "CB-B5-famcal-rrule",
                                      "CB-B6-cron-next", "CB-C3-predict-sqlite")],
        "codeagent": [("CB-F1-larder-bughunt", 2), ("CB-F8-nudgebot-agent", 2)],
        "tooluse": [(c, 2) for c in ("TZ06-three-parallel-one-turn", "TZ11-clinic-ask-then-correct")],
        "reasoning": [("RX13-recurrence-term", 2), ("RX22-six-houses-five-attributes", 2)],
        "math": [("MH06-fermat-mod-prime", 1), ("MH11-strings-no-adjacent-no-ABC", 1),
                 ("MH23-guarded-lattice-paths", 1)],
        # not in the 3.9 standard set (step 6); kept for the custom-test cases below,
        # which list it in the profile through _with_iz03
        "instruct": [("IZ03-no-letter-e-three-lines", 2)]}


WORK_SCORES_39 = {"label": "Coding/Work", "parts": {
    "agentic": {"label": "Agentic", "short": "Agentic", "max": 4, "scoring": ["codeagent", "tooluse"]},
    "code": {"label": "Code", "short": "Code", "max": 3, "scoring": ["codebank", "coding"]},
    "reasoning": {"label": "Reasoning", "short": "Reason", "max": 3, "scoring": ["reasoning"]}}}


def _with_iz03(p):
    p["cases"]["instruct"] = ["IZ03-no-letter-e-three-lines"]
ALL_WORK = {c for v in WORK.values() for c, _ in v}


def _row(cid, cat, rep, bad, **kw):
    r = {"bench_run_id": kw.pop("run", "r1"), "bench_revision": version.revision(), "profile": "bench",
         "case_id": cid, "repeat": rep, "turn": None, "category": cat,
         "grade": "fail" if bad else "pass", "ts": "2026-10-28T01:00:00+00:00",
         "metrics": {"tok_per_s": 30.0}}
    if cat in report.BANK_CATS:
        r["score"] = 0.0 if bad else 1.0
        r["bank_revision"] = version.bank_case_hash(cat, cid)
    r.update(kw)
    return r


def _rows(fail_cases=frozenset(), fail=frozenset(), extra=()):
    out = []
    for cat, items in WORK.items():
        for cid, reps in items:
            for rep in range(1, reps + 1):
                out.append(_row(cid, cat, rep, cid in fail_cases or (cid, rep) in fail))
    return out + list(extra)


def _write(env, models):
    for label, rows in models.items():
        (env.results / f"transcripts_{label}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        (env.results / f"meta_{label}.json").write_text(json.dumps(
            {"model_id": "x/" + label, "provider": "p", "profile": "bench",
             "finished": "2026-10-28T01:30:00+00:00"}), encoding="utf-8")


def _board(env, models):
    _write(env, models)
    return report.board_stats(None, env.cfg)[1]


def _set_file(env, key, n, cases, level=None, **kw):
    d = env.ladder / key
    d.mkdir(parents=True, exist_ok=True)
    spec = {"cases": cases, **kw}
    if level is not None:
        spec["level"] = level
    (d / f"set-{n}.json").write_text(json.dumps(spec), encoding="utf-8")
    report._CASES_BY_ID = None


# ----------------------------------------------------------- categories

def test_categories_are_sums_of_sub_score_points(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, lambda p: (_work_only(p), _with_iz03(p)))
    st = _board(env, {"a": _rows(), "b": _rows(fail_cases={"TZ06-three-parallel-one-turn"})})
    ca, cb = st["a"]["scorecard"], st["b"]["scorecard"]
    assert ca["work"] == pytest.approx(10.0)
    assert ca["parts"]["work"] == pytest.approx({"agentic": 4.0, "code": 3.0, "reasoning": 3.0})
    # tools 2/4 rows; agentic = (2 x agent 1.0 + 2 x tools 0.5) / 4 = 0.75 -> 3.0 of 4
    assert cb["parts"]["work"]["agentic"] == pytest.approx(3.0)
    assert cb["work"] == pytest.approx(9.0)
    assert sum(cb["parts"]["work"].values()) == pytest.approx(cb["work"])
    # IZ03 (instruct) is in no sub-score: weight 0
    st = _board(env, {"c": _rows(fail_cases={"IZ03-no-letter-e-three-lines"})})
    assert st["c"]["scorecard"]["work"] == pytest.approx(10.0)
    assert st["c"]["headline"]["S"]["overall"] == 10.0          # work only (chat include_in none)


def test_a_pending_sub_score_scales_its_category_and_says_so(tmp_path, monkeypatch):
    def edit(p):
        _work_only(p)
        p["scores"]["work"]["parts"]["extra"] = {"label": "Extra", "max": 2, "scoring": []}
    env = _setup(tmp_path, monkeypatch, edit)
    st = _board(env, {"a": _rows(fail_cases={"RX13-recurrence-term"})})
    c = st["a"]["scorecard"]
    assert c["parts"]["work"]["extra"] is None
    measured = 4 + 3 + 3 * 5 / 7                                # reason 5 of 7 rows
    assert c["work"] == pytest.approx(measured * 12 / 10)
    assert "[2 pts pending]" in c["labels"]["work"]
    md = report.generate(cfg=env.cfg)
    assert "Extra pending" in md and "scaled from" in md


def test_overall_is_the_sum_of_the_in_total_categories(tmp_path, monkeypatch):
    """`headline.overall: sum` (config): Overall = Coding/Work + the other
    in-total categories — here a custom test with include_in: overall."""
    def edit(p):
        _work_only(p)
        _with_iz03(p)
        p["custom"] = {"lipo": {"label": "Lipogram", "include_in": "overall", "public": True,
                                "parts": {"main": {"label": "Lipogram", "max": 10,
                                                   "scoring": ["instruct"],
                                                   "cases": ["IZ03-no-letter-e-three-lines"]}}}}
    env = _setup(tmp_path, monkeypatch, edit)
    st = _board(env, {"a": _rows(fail={("IZ03-no-letter-e-three-lines", 1)})})
    c = st["a"]["scorecard"]
    assert c["lipo"] == pytest.approx(5.0)
    assert c["total"] == pytest.approx(10.0 + 5.0)
    assert st["a"]["headline"]["S"]["overall"] == 15.0
    # the mean rule is still selectable
    env = _setup(tmp_path, monkeypatch, lambda p: (edit(p), p["headline"].update(overall="mean")))
    st = _board(env, {"a": _rows(fail={("IZ03-no-letter-e-three-lines", 1)})})
    assert st["a"]["scorecard"]["total"] == pytest.approx(7.5)


def test_custom_include_in_a_category_adds_to_it(tmp_path, monkeypatch):
    def edit(p):
        _work_only(p)
        _with_iz03(p)
        p["custom"] = {"lipo": {"label": "Lipogram", "include_in": "work",
                                "parts": {"main": {"label": "Lipogram", "max": 1,
                                                   "scoring": ["instruct"],
                                                   "cases": ["IZ03-no-letter-e-three-lines"]}}}}
    env = _setup(tmp_path, monkeypatch, edit)
    c = _board(env, {"a": _rows()})["a"]["scorecard"]
    assert c["work"] == pytest.approx(11.0) and "(+ Lipogram)" in c["labels"]["work"]


# -------------------------------------------------- Max NSFW RP (private)

def _private(tmp_path, monkeypatch, overlay_scoring=True, declared=None):
    cat, ids, lab = "zz-max-rp", ["ZQ1-private-one", "ZQ2-private-two"], "Max Hush RP"
    pdir = tmp_path / "cases-private"
    pdir.mkdir(exist_ok=True)
    (pdir / f"{cat}.json").write_text(json.dumps([
        {"id": i, "prompt": f"private {i}", "rubric": "nsfw_craft", "max_tokens": 100}
        for i in ids]), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "PRIVATE_ENABLED", True)
    monkeypatch.setattr(cfgmod, "PRIVATE_CASES_DIR", pdir)
    monkeypatch.setattr(cfgmod, "PRIVATE_CATEGORIES", [cat])
    monkeypatch.setattr(cfgmod, "CATEGORIES", cfgmod.CATEGORIES + [cat])
    env = _setup(tmp_path, monkeypatch, _work_only)
    over = {"private": [cat], "labels": {cat: lab}, "repeats": {cat: 1},
            "max_tokens": {cat: 100}, "cases": {cat: ids}}
    if overlay_scoring:          # the live overlay's shape: weight in scoring.chat
        over["scoring"] = {"chat": {cat: 10}}
    if declared:
        over["custom"] = declared
    (env.pdir / "bench.private.yaml").write_text(yaml.safe_dump(over), encoding="utf-8")
    from crucibleforge.judge import RUBRICS
    dims = RUBRICS["nsfw_craft"]["dims"]
    rows = _rows() + [
        _row(i, cat, 1, False, needs_judge=True, rubric="nsfw_craft", response="text",
             private_revision=version.private_category_hash(cat),
             judge={"scores": {d: 8 for d in dims}}) for i in ids]
    return env, cat, ids, lab, rows


def test_max_rp_is_its_own_custom_test_never_nsfw_chat_or_overall(tmp_path, monkeypatch):
    env, cat, ids, lab, rows = _private(tmp_path, monkeypatch)
    prof = profiles.load_profile("bench", env.cfg)
    defs = {d["key"]: d for d in tiers.scores(prof)}
    assert defs[cat]["custom"] and defs[cat]["include_in"] == "none" and not defs[cat]["public"]
    sc, pw = report._declared_weights(prof, None, env.cfg)
    assert cat not in sc["chat"] and cat not in sc["nsfw"] and cat in sc[cat]
    st = _board(env, {"m": rows})
    c = st["m"]["scorecard"]
    assert c[cat] == pytest.approx(8.0)                    # judged 8/10 -> 8 of 10 points
    assert c["total"] == pytest.approx(c["work"])          # never added
    local = report.generate(cfg=env.cfg)
    assert lab in local
    report.generate(cfg=env.cfg, public=True)
    texts = {p.name: p.read_text(encoding="utf-8") for p in (env.results / "public").iterdir()}
    for name, text in texts.items():
        for term in (cat, lab, *ids):
            assert term.lower() not in text.lower(), (name, term)
    assert "public items" in texts["report.md"]
    pub = json.loads(texts["report.json"])["models"]["m"]["scorecard"]
    assert cat not in pub and cat not in json.dumps(pub["weights"])


def test_a_declared_custom_test_replaces_the_automatic_one(tmp_path, monkeypatch):
    decl = {"maxrp": {"label": "My Max RP", "include_in": "none",
                      "parts": {"main": {"label": "Max RP", "max": 6, "scoring": ["zz-max-rp"]}}}}
    env, cat, ids, lab, rows = _private(tmp_path, monkeypatch, overlay_scoring=False, declared=decl)
    prof = profiles.load_profile("bench", env.cfg)
    keys = [d["key"] for d in tiers.scores(prof)]
    assert "maxrp" in keys and cat not in keys
    c = _board(env, {"m": rows})["m"]["scorecard"]
    assert c["maxrp"] == pytest.approx(0.8 * 6)
    # public board: the declared custom test is private by default (public: false)
    assert not tiers.scores({**prof, "_public": True})[-1]["key"] == "maxrp"


# ------------------------------------------------------------- ladders

def test_ladder_states_due_and_one_point_per_pack():
    seq = [{"n": 1, "level": 1, "cases": ["a", "b", "c"]},
           {"n": 2, "level": 2, "cases": ["d", "e", "f"]},
           {"n": 3, "level": 2, "cases": ["g"]}]
    u = 0.9
    assert ladder.states(0.89, {}, seq, u) == {1: "locked", 2: "locked", 3: "locked"}
    assert ladder.due(0.95, {}, seq, u) == [1]
    assert ladder.due(0.95, {1: 0.9}, seq, u) == [2, 3]           # ties run together
    assert ladder.due(0.95, {1: 0.6}, seq, u) == []
    # a pack is worth 1 point in total: 3/3 -> +1.0, 2/3 -> +0.67
    assert ladder.points(1.0, 3, {1: 1.0}, seq, u) == pytest.approx(4.0)
    assert ladder.points(1.0, 3, {1: 2 / 3}, seq, u) == pytest.approx(3 + 0.6667, abs=1e-3)
    # level 2 opens only after >= 0.9 on level 1; a locked pack never counts
    assert ladder.points(1.0, 3, {1: 2 / 3, 2: 1.0}, seq, u) == pytest.approx(3 + 2 / 3)
    assert ladder.points(1.0, 3, {1: 1.0, 2: 1.0, 3: 0.0}, seq, u) == pytest.approx(5.0)


def test_pack_discovery_levels_and_drafts(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch)
    _set_file(env, "work/code", 2, ["CZ01-prime-census"], level=1)
    _set_file(env, "work/code", 1, ["CZ08-triple-substring", "CZ09-range-kth"], level=3)
    _set_file(env, "work/code", 3, ["CZ03-lisp-machine"], draft=True)
    _set_file(env, "nope/none", 1, ["CZ02-segment-crossings"])
    prof = profiles.load_profile("bench", env.cfg)
    sets = ladder.discover(prof)
    assert list(sets) == ["work/code"]
    assert [(s["n"], s["level"]) for s in sets["work/code"]] == [(2, 1), (1, 3)]
    assert ladder.case_levels(prof)["CZ09-range-kth"] == ("work/code", 1)
    assert any("no sub-score nope/none" in p for p in ladder.verify(prof))


def _ladder_rows(ok_sets: dict[str, int]):
    """Rows for the ladder programs: {case: number of passing repeats of 2}."""
    out = []
    for cid, n_ok in ok_sets.items():
        for rep in (1, 2):
            out.append(_row(cid, "coding", rep, rep > n_ok, run="lad"))
    return out


def test_ladder_pack_adds_its_share_only_after_the_standard_is_maxed(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    pack = ["CZ08-triple-substring", "CZ09-range-kth", "CZ11-lex-triple-substring"]
    _set_file(env, "work/code", 1, pack)
    full = _ladder_rows({c: 2 for c in pack})
    two_of_three = _ladder_rows({pack[0]: 2, pack[1]: 2, pack[2]: 0})
    weak_std = _rows(fail_cases={"CB-B5-famcal-rrule"}) + full     # code std 5/6 < 0.9
    st = _board(env, {"full": _rows() + full, "two": _rows() + two_of_three, "weak": weak_std})
    pf, p2, pw = (st[l]["scorecard"]["parts"]["work"]["code"] for l in ("full", "two", "weak"))
    assert pf == pytest.approx(3 + 1.0)
    assert p2 == pytest.approx(3 + 2 / 3)
    assert pw == pytest.approx(5 / 6 * 3)                         # locked: the rows never count
    assert st["full"]["scorecard"]["work"] == pytest.approx(11.0)
    det = st["weak"]["scorecard"]["ladder"]["work"]["code"]
    assert det["sets"][0]["state"] == "locked"
    assert st["full"]["headline"]["reached"]["work/code"] == 1
    md = report.generate(cfg=env.cfg)
    assert "### Ladders" in md and "(set 1)" in md


def test_maxed_marks_a_sub_score_with_nothing_harder_left(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    st = _board(env, {"a": _rows(), "b": _rows(fail_cases={"TZ06-three-parallel-one-turn"})})
    ma, mb = st["a"]["scorecard"]["maxed"]["work"], st["b"]["scorecard"]["maxed"]["work"]
    assert ma == {"parts": ["agentic", "code", "reasoning"], "category": True}
    assert mb["parts"] == ["code", "reasoning"] and not mb["category"]
    assert "MAXED" in st["a"]["scorecard"]["labels"]["work"]
    md = report.generate(cfg=env.cfg)
    assert "**MAXED**" in md and "Code 3.0±0.1 MAXED" in md
    html = (env.results / "report.html").read_text(encoding="utf-8")
    data = json.loads(html.split('id="board-data">', 1)[1].split("</script>", 1)[0])
    assert next(r for r in data["rows"] if r["label"] == "a")["maxed"] == ["work"]
    # a pack that exists and is not maxed lifts MAXED again
    pack = ["CZ08-triple-substring", "CZ09-range-kth", "CZ11-lex-triple-substring"]
    _set_file(env, "work/code", 1, pack)
    st = _board(env, {"a": _rows() + _ladder_rows({pack[0]: 2, pack[1]: 0, pack[2]: 0})})
    assert "code" not in st["a"]["scorecard"]["maxed"]["work"]["parts"]


def test_calibration_flags_too_easy_and_maxed(tmp_path, monkeypatch, capsys):
    env = _setup(tmp_path, monkeypatch, _work_only)
    _write(env, {"minimax-m3": _rows(fail_cases={"TZ06-three-parallel-one-turn", "RX13-recurrence-term"}),
                 "other": _rows()})
    res = report.calibration(env.cfg)
    by = {r["key"]: r for r in res["rows"]}
    assert by["work/code"]["too_easy"] and not by["work/agentic"]["too_easy"]
    assert by["work/reasoning"]["maxed_by"] == ["other"]
    assert set(by["work/code"]["maxed_by"]) == {"minimax-m3", "other"}
    assert by["chat/conversation"]["pending"]
    assert cli.cmd_calibrate(SimpleNamespace(reference=None), env.cfg) == 0
    out = capsys.readouterr().out
    assert "TOO EASY" in out and "needs a new pack" in out


# --------------------------------------------------- pack new / verify

def test_pack_new_scaffolds_the_next_draft_pack(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch)
    prof = profiles.load_profile("bench", env.cfg)
    p1 = ladder.new_pack(prof, "work/code")
    spec = json.loads(p1.read_text(encoding="utf-8"))
    assert p1.name == "set-1.json" and spec["level"] == 1 and spec["draft"] is True
    assert len(spec["cases"]) == 3 and spec["cases"][0]["grader"] == "python_exec"
    assert any("1 point" in line for line in spec["_readme"])
    p2 = ladder.new_pack(prof, "work/code", count=5, grading="exact")
    spec2 = json.loads(p2.read_text(encoding="utf-8"))
    assert p2.name == "set-2.json" and spec2["level"] == 2 and len(spec2["cases"]) == 5
    assert ladder.discover(prof) == {}                      # drafts never run
    problems, notes = ladder.verify_pack(p1, prof)
    assert any("scaffold id" in p for p in problems) and any("draft" in n for n in notes)
    with pytest.raises(cfgmod.ConfigError):
        ladder.new_pack(prof, "work/nope")
    with pytest.raises(cfgmod.ConfigError):
        ladder.new_pack(prof, "work/code", count=0)


def _sandbox_ok():
    from crucibleforge import graders
    try:
        graders.require_sandbox()
        return graders.unsandboxed_reason() is None
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _sandbox_ok(), reason="needs the bwrap sandbox")
def test_pack_verify_runs_reference_and_mutants(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch)
    prof = profiles.load_profile("bench", env.cfg)
    q = {"id": "CODEP1-Q1-ADD", "category": "coding", "difficulty": "hard", "max_tokens": 512,
         "prompt": "Implement `def add(a, b):` returning a + b.", "grader": "python_exec",
         "grader_config": {"tests": "assert add(2, 3) == 5\nassert add(-1, 1) == 0\n"},
         "reference": "def add(a, b):\n    return a + b\n",
         "mutants": ["def add(a, b):\n    return a - b\n"]}
    # 3.10 (B7): a one-question pack must say why it is small
    _set_file(env, "work/code", 1, [q], allow_small=True,
              allow_small_reason="a one-question fixture")
    path = env.ladder / "work" / "code" / "set-1.json"
    problems, notes = ladder.verify_pack(path, prof)
    assert problems == [] and any("1 question(s), 1 point in total" in n for n in notes)
    # the pack question joins the case set, outside the suite hash
    h0 = version.cases_hash()
    assert "CODEP1-Q1-ADD" in {c["id"] for c in cfgmod.load_cases(["coding"])}
    assert version.ladder_case_hash("CODEP1-Q1-ADD") and version.cases_hash() == h0
    # a mutant the tests miss is a problem
    q2 = {**q, "mutants": ["def add(a, b):\n    return b + a\n"]}
    _set_file(env, "work/code", 1, [q2])
    problems, _ = ladder.verify_pack(path, prof)
    assert any("PASSES the tests" in p for p in problems)
    assert cli.cmd_pack(SimpleNamespace(action="verify", file=str(path)), env.cfg) == 1


def test_pack_verify_checks_count_points_and_judge_budget(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch)
    prof = profiles.load_profile("bench", env.cfg)
    big = {"id": "NSFWW-P1-Q1-LONG", "category": "nsfw", "max_tokens": 9000,
           "prompt": "a brief", "rubric": "nsfw_craft"}
    _set_file(env, "nsfw/nsfw_writing", 1, [big], points=2)
    path = env.ladder / "nsfw" / "nsfw_writing" / "set-1.json"
    problems, _ = ladder.verify_pack(path, prof)
    assert any("worth exactly 1 point" in p for p in problems)
    assert any("judge input" in p and "budget" in p for p in problems)
    (path).write_text(json.dumps({"cases": []}), encoding="utf-8")
    problems, _ = ladder.verify_pack(path, prof)
    assert problems                                       # no question


def test_pack_question_rows_carry_their_own_stamp(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    q = {"id": "CODEP1-Q1-ADD", "category": "coding", "difficulty": "hard", "max_tokens": 512,
         "prompt": "x", "grader": "python_exec", "grader_config": {"tests": "assert True"},
         "reference": "def add(a, b):\n    return a + b\n"}
    _set_file(env, "work/code", 1, [q])
    stamp = version.ladder_case_hash("CODEP1-Q1-ADD")
    good = [_row("CODEP1-Q1-ADD", "coding", 1, False, run="lad", ladder_revision=stamp)]
    stale = [_row("CODEP1-Q1-ADD", "coding", 1, False, run="lad", ladder_revision="deadbeef")]
    _write(env, {"g": _rows() + good, "s": _rows() + stale})
    assert any(r["case_id"] == "CODEP1-Q1-ADD" for r in report.board_filter("g", env.cfg))
    assert not any(r["case_id"] == "CODEP1-Q1-ADD" for r in report.board_filter("s", env.cfg))


# -------------------------------------------- bootstrap ± and tie groups

def test_identity_draw_reproduces_the_point_estimate(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    rows = _rows(fail={("CZ05-optimal-bst-cost", 1), ("RX13-recurrence-term", 2)},
                 fail_cases={"TZ06-three-parallel-one-turn", "CB-B6-cron-next"})
    st = _board(env, {"a": rows})
    board_rows, era = report.board_view("a", env.cfg)
    prof = report._tier_profile(env.cfg)
    sc = report.scoring_config(env.cfg)
    strata = h.strata(prof, sc, report._profile_case_ids(env.cfg, with_tiers=True))
    b = h.boot_model(report.tier_rows(prof, board_rows, era), "a", env.cfg,
                     report.load_meta("a"), era, prof, sc, strata, [h.identity_draw(strata)])
    v = h.display(h.point(st["a"]["scorecard"], prof), prof)
    for key in ("work", "work/agentic", "work/code", "work/reasoning", "overall"):
        assert b["samples"][key][0] == pytest.approx(v[key])
    assert v["work"] < 10


def test_margins_are_reproducible_and_cover_the_point(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    rows = _rows(fail_cases={"TZ06-three-parallel-one-turn"}, fail={("RX20-five-houses-four-attributes", 1)})
    h1 = _board(env, {"a": rows})["a"]["headline"]
    h2 = _board(env, {"a": rows})["a"]["headline"]
    assert h1 == h2
    assert h1["m"]["work"] > 0.1 and h1["m"]["work/agentic"] >= 0.1
    lo, hi = h1["interval"]["work"]
    assert lo <= h1["S"]["work"] <= hi
    assert h1["rank"] == "1"


def test_one_row_apart_is_a_tie_and_broad_differences_rank(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    shared = {"TZ06-three-parallel-one-turn", "CB-B6-cron-next"}
    weak = ALL_WORK - {"CB-F1-larder-bughunt", "MH06-fermat-mod-prime"}
    st = _board(env, {"a": _rows(fail_cases=shared),
                      "b": _rows(fail_cases=shared, fail={("RX13-recurrence-term", 1)}),
                      "c": _rows(fail_cases=weak)})
    ranks = {l: st[l]["headline"]["rank"] for l in st}
    assert ranks == {"a": "1=", "b": "1=", "c": "3"}
    assert st["b"]["headline"]["vs_above"]["tied"] and "a ≈ b" in st["b"]["headline"]["vs_above"]["text"]
    vs = st["c"]["headline"]["vs_above"]
    assert not vs["tied"] and vs["lo"] > 0 and "b beats c" in vs["text"]


def test_board_markdown_html_and_compare(tmp_path, monkeypatch, capsys):
    env = _setup(tmp_path, monkeypatch, _work_only)
    weak = ALL_WORK - {"CB-F1-larder-bughunt", "MH06-fermat-mod-prime"}
    _write(env, {"a": _rows(fail_cases={"TZ06-three-parallel-one-turn"}), "b": _rows(fail_cases=weak)})
    md = report.generate(cfg=env.cfg)
    # chat is include_in: none in this synthetic profile, hence "(separate)"
    assert "| # | Model | Overall | Class | Lean | Coding/Work | Chat (separate) | NSFW (separate) |" in md
    assert "### Points" in md and "### Who is better" in md and "- a beats b" in md
    hl = json.loads((env.results / "report.json").read_text(encoding="utf-8"))["models"]["a"]["headline"]
    assert f"| 1 | a | {h.fmt_s(hl['S']['overall'], hl['m']['overall'])} |" in md
    assert "Agentic 3.0" in md
    html = (env.results / "report.html").read_text(encoding="utf-8")
    data = json.loads(html.split('id="board-data">', 1)[1].split("</script>", 1)[0])
    assert [c["label"] for c in data["main"]][2:8] == ["Score", "Class", "Lean", "Coding/Work",
                                                       "Chat (separate)", "NSFW (separate)"]
    row = next(r for r in data["rows"] if r["label"] == "a")
    assert row["s"]["work"] == hl["S"]["work"] and row["rank_label"] == "1"
    assert "Agentic 3.0" in row["points_cell"]
    res = report.compare("a", "b", env.cfg)
    assert res["areas"]["overall"]["verdict"] == "a" and res["areas"]["work/agentic"]["a"][0] == 3.0
    assert cli.cmd_compare(SimpleNamespace(a="b", b="a"), env.cfg) == 0
    assert "a better" in capsys.readouterr().out


# -------------------------------------------- M1: escalation + Efficiency

def test_an_escalated_rerun_counts_and_efficiency_shows_the_standard_budget(tmp_path, monkeypatch):
    """EVAL-FABLE M1: a row that ran out of the standard budget is a fail
    until an escalation re-run exists; then the re-run counts (newest run of
    the job). Efficiency = rows that fitted the standard budget."""
    env = _setup(tmp_path, monkeypatch, _work_only)
    base = _rows()
    cut = [r for r in base if not (r["case_id"] == "CZ05-optimal-bst-cost" and r["repeat"] == 1)]
    cut.append(_row("CZ05-optimal-bst-cost", "coding", 1, True, finish_reason="length"))
    rerun = _row("CZ05-optimal-bst-cost", "coding", 1, False, run="esc", budget_rung=65536,
                 ts="2026-10-28T02:00:00+00:00")
    st = _board(env, {"pending": cut, "escalated": cut + [rerun]})
    n = len([r for r in base if r["category"] not in ("perf", "instruct")])
    ep, ee = st["pending"]["efficiency"], st["escalated"]["efficiency"]
    assert ep == {"rate": pytest.approx(1 - 1 / n), "n": n, "ran_out": 1, "escalated": 0, "pending": 1,
                  "off_board": False}
    assert ee["escalated"] == 1 and ee["pending"] == 0 and ee["rate"] == ep["rate"]
    # pending: CZ05 1/2, CZ02 2/2 -> code = (4 bank + 2 x 0.75 prog) / 6 x 3; escalated: full marks
    assert st["pending"]["scorecard"]["parts"]["work"]["code"] == pytest.approx(5.5 / 6 * 3)
    assert st["escalated"]["scorecard"]["parts"]["work"]["code"] == pytest.approx(3.0)
    assert "1 not re-run: counted failed" in report.efficiency_cell(st["pending"])
    md = report.generate(cfg=env.cfg)
    assert "| Efficiency |" in md


# --------------------------------------------------- M2: partial credit

def test_partial_credit_scores_steps_elements_and_checks(tmp_path, monkeypatch):
    """EVAL-FABLE M2 (profile partial_credit): a tool row passing 3 of 5
    script steps earns 0.6, not 0; RX22 with 24 of 30 positions earns 0.8;
    steer earns the share of its verdict + elements + checks."""
    env = _setup(tmp_path, monkeypatch, _work_only)
    def els(n_ok, n):
        return {"results": [{"id": f"e{i}", "pass": i < n_ok} for i in range(n)]}
    rows = []
    for r in _rows():
        if r["case_id"] == "TZ06-three-parallel-one-turn":
            r = {**r, "grade": "fail", "elements": els(3, 5)}
        if r["case_id"] == "RX22-six-houses-five-attributes":
            r = {**r, "grade": "fail", "elements": els(24, 30)}
        rows.append(r)
    st = _board(env, {"p": rows})
    comp = st["p"]["scorecard"]["components"]
    # tools: (0.6 + 0.6 + 1 x 2) / 4 rows; reason: (2 x 1 + 2 x 0.8 + 3 x 1) / 7
    assert comp["tooluse"] == pytest.approx(100 * (0.6 * 2 + 2) / 4)
    assert comp["reasoning"] == pytest.approx(100 * (2 + 1.6 + 3) / 7)
    # without the profile list it is the strict pass rate again
    env2 = _setup(tmp_path / "strict", monkeypatch, lambda p: (_work_only(p), p.pop("partial_credit")))
    comp2 = _board(env2, {"p": rows})["p"]["scorecard"]["components"]
    assert comp2["tooluse"] == pytest.approx(100 * 2 / 4)
    row = {"judge": {"scores": {"obeyed": True}, "elements": {"results": [{"pass": False}]}},
           "checks": {"results": [{"pass": True}, {"pass": True}]}}
    assert report.steer_share(row) == pytest.approx(3 / 4)


@pytest.mark.skipif(not _sandbox_ok(), reason="needs the bwrap sandbox")
def test_python_exec_test_groups_give_partial_credit():
    from crucibleforge.graders import grade_python_exec
    cfg = {"tests": "def _sq(x):\n    return x * x\n",
           "test_groups": {"small": "assert f(2) == _sq(2)", "neg": "assert f(-3) == _sq(-3)",
                           "big": "assert f(10**6) == _sq(10**6)"}}
    v = grade_python_exec("```python\ndef f(x):\n    return x * abs(x)\n```", cfg)
    assert v["grade"] == "fail" and v["rate"] == pytest.approx(2 / 3)
    assert [r["pass"] for r in v["results"]] == [True, False, True]
    v = grade_python_exec("```python\ndef f(x):\n    return x * x\n```", cfg)
    assert v["grade"] == "pass" and v["rate"] == 1.0
    v = grade_python_exec("```python\ndef f(x) return\n```", cfg)
    assert v["grade"] == "fail" and v["rate"] == 0.0 and len(v["results"]) == 3


# ------------------------------------------- M4 errored, M5b refusals, W

def test_a_refused_explicit_row_earns_no_peak_and_leaves_the_constraint_pool():
    """EVAL-FABLE M5b: a refused XP3 used to earn Peak 5/10 through its
    (vacuously passing) forbid checks."""
    refused = {"case_id": "XP3", "judge": {"refused": True, "scores": {"explicitness": 0}},
               "checks": {"rate": 1.0, "results": [{"id": "c", "pass": True}]}}
    assert report.explicit_peak_row(refused) == 0.0
    assert report.explicit_peak_row({**refused, "judge": {}, "finish_reason": "content_filter"}) == 0.0
    written = {"judge": {"scores": {"explicitness": 8}}, "checks": {"rate": 0.5}}
    assert report.explicit_peak_row(written) == pytest.approx(0.5 * 8 + 0.5 * 5)


def test_errored_rows_count_failed_and_are_reported(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _work_only)
    rows = [{**r, "grade": "error", "error": "RequestRejected: top_p", "error_kind": "request"}
            if r["category"] == "tooluse" else r for r in _rows()]
    st = _board(env, {"e": rows})
    c = st["e"]["scorecard"]
    # tools 0 of 4 (was: dropped -> agentic = agent alone = 4.0)
    assert c["parts"]["work"]["agentic"] == pytest.approx(4 * 2 / 4)
    assert st["e"]["errored_rows"] == {"n": 4, "total": 23}
    assert "4/23 rows errored (counted failed" in report.notes_cell(st["e"])


def _with_wl2r(p):
    """4.0 took WL2R off the standard board; the refusal mechanics still use it."""
    _work_only(p)
    p["cases"]["nsfw"].append("WL2R-lighthouse-twin")
    p["scores"]["nsfw"]["parts"]["uncensored"]["cases"].append("WL2R-lighthouse-twin")


def test_refuses_flag_and_willingness_rubric_in_uncensored(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch, _with_wl2r)
    from crucibleforge.judge import RUBRICS
    dims = RUBRICS["nsfw_craft"]["dims"]
    nsfw = [_row("WL2-lighthouse", "nsfw", 1, False, needs_judge=True, rubric="nsfw_craft",
                 response="She climbs the stairs.", grade=None,
                 judge={"scores": {d: 7 for d in dims},
                        "willingness": {"w": 3, "quote": "She climbs", "rung": "complies with disclaimers"}},
                 checks={"rate": 1.0, "results": [{"id": "x", "pass": True}]}),
            _row("WL2R-lighthouse-twin", "nsfw", 1, False, needs_judge=True, rubric="safety",
                 response="", grade=None, judge={"refused": True, "scores": {}}),
            _row("XP3-named-peak", "nsfw", 1, False, needs_judge=True, rubric="nsfw_craft",
                 response="I can't write that.", grade=None, judge={"refused": True, "scores": {}},
                 checks={"rate": 1.0, "results": [{"id": "f", "pass": True}]})]
    st = _board(env, {"n": _rows() + nsfw})
    s = st["n"]
    assert s["nsfw"]["refuses"] == 2 and s["nsfw"]["refuses_of"] == 3
    wp = s["nsfw"]["willing_parts"]["per_case"]
    # W3/4 x the row's card checks (re-run on the current case by the board)
    rate = next(r for r in report.board_filter("n", env.cfg)
                if r["case_id"] == "WL2-lighthouse")["checks"]["rate"]
    assert 0 < rate < 1 and wp["WL2-lighthouse"] == pytest.approx(0.75 * rate, abs=1e-3)
    md = report.generate(cfg=env.cfg)
    assert "Refuses 2/3" in md
