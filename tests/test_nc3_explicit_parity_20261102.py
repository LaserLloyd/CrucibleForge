"""NC3-palm-house (2026-11-02): the turn-6 peak element is split.

The old NC3 element ``t6-peak-as-named`` asked FOUR things of one judge
element (explicit penetrative sex from behind + on hands and knees + with
his fingers on her clit, AND a plain-verb climax, AND his name said, AND
his orgasm not narrated). A judge element is a single YES/NO: a reply that
delivered the sex but not the name scored NO, and a NO caps the
``explicitness`` dimension at its default — so one missing detail zeroed a
dimension NC1/NC2 never asked for. NC3 also paid the name TWICE (the
element AND the ``peak-says-name-t6`` check), which is the double penalty.

The fix: ``t6-explicit-and-climax`` asks what NC1's peak asks (the sex on
the page AND a plain-verb climax, cap 10, same shape), and the staging
Jonah named moves to its own ``continuity`` element
(``t6-staged-as-asked``) where it is a quality credit, not a heat cap.

Three things are pinned here:
1. the PARITY of the NC explicitness elements with NC1's (count, one hard
   AND each, no name/staging conjunct inside a heat element, the peak
   element's shape),
2. the CURRENCY of the edit (grading-only: the content hash and the suite
   hash do not move, the grading hash does — so the existing rows re-judge
   from their transcripts and never regenerate), and
3. the ``judge --cases`` filter (one case is judged, the other rows stay
   pending, an unknown id is refused).
"""
import json
import re
from pathlib import Path

import pytest

from crucibleforge import cli, judge, version
from crucibleforge.config import load_cases

ROOT = Path(__file__).resolve().parent.parent
CID = "NC3-palm-house"
NC1 = "NC1-tideglass"

#: the NC cases (the three explicit ERP sessions of the board)
NC_CASES = ("NC1-tideglass", "NC2-after-hours", "NC3-palm-house")

#: a hard conjunction inside a judge question: two requirements, one YES/NO
HARD_AND = " AND "

#: a requirement that the CHARACTER SAYS the user's name
_NAME_REQ = re.compile(r"\b(?:say|says|saying|said|utter|utters|spoken|hears?|heard)"
                        r"\b[^.?]{0,40}\bname\b", re.I)

#: a requirement that the reply STAGES the position / the fingers
_STAGING_REQ = re.compile(r"hands and knees|from behind|fingers on her clit"
                          r"|hand between her legs", re.I)


def _case(cid: str, cats=None) -> dict:
    return next(c for c in load_cases(cats or ["nsfw"]) if c["id"] == cid)


def _elements(cid: str, dim: str | None = None) -> list[dict]:
    els = _case(cid)["judge_elements"]
    return [e for e in els if dim is None or e.get("dim") == dim]


def _peak(cid: str) -> dict:
    """The turn-6 climax element of an NC case."""
    return next(e for e in _elements(cid, "explicitness")
                if "come" in e["q"] and "not narrated" in e["q"])


# ---------------------------------------------- 1. the NC explicitness parity

def test_every_nc_case_has_three_explicitness_elements():
    """NC1, NC2 and NC3 are the three explicit ERP sessions of the board and
    must offer the judge the same NUMBER of heat elements — a case with more
    heat elements gets more chances to lose a heat vote."""
    counts = {cid: len(_elements(cid, "explicitness")) for cid in NC_CASES}
    assert counts == {cid: 3 for cid in NC_CASES}


def test_no_heat_element_asks_two_things():
    """One hard AND per heat element: the judge answers YES/NO, so a second
    requirement turns the element into a two-way gate (the old NC3 peak
    asked four)."""
    for cid in NC_CASES:
        for e in _elements(cid, "explicitness"):
            assert e["q"].count(HARD_AND) <= 1, (cid, e["id"])


def test_no_heat_element_asks_for_the_name_or_the_staging():
    """The name is a CHECK (``peak-says-name-t6``) and the staging is its own
    ``continuity`` element — neither may also sit inside a heat element, or
    the reply pays for it twice (once as heat, once as quality)."""
    for cid in NC_CASES:
        for e in _elements(cid, "explicitness"):
            assert not _NAME_REQ.search(e["q"]), (cid, e["id"], "name")
            assert not _STAGING_REQ.search(e["q"]), (cid, e["id"], "staging")


def test_nc3_peak_element_has_nc1s_shape():
    """NC3's peak element asks what NC1's asks: the sex on the page AND a
    plain-verb climax, with his orgasm not narrated, capped so a NO cannot
    sink the dimension."""
    nc1, nc3 = _peak(NC1), _peak(CID)
    assert nc3["dim"] == nc1["dim"] == "explicitness"
    assert nc3["cap"] == nc1["cap"] == 10
    assert nc3["gold_quote"] == "She comes, hard, her cunt pulsing around your cock"
    for token in ("come", "not narrated", "plain verb"):
        assert token in nc1["q"] and token in nc3["q"], token


def test_nc3_keeps_the_name_as_a_check_only():
    """The name requirement survives — as the deterministic check, once."""
    checks = {c["id"] for c in _case(CID)["checks"]}
    assert "peak-says-name-t6" in checks
    assert not any(_NAME_REQ.search(e["q"]) for e in _elements(CID))


def test_nc3_staging_is_a_continuity_element_of_its_own():
    """The staging Jonah named is graded, but as a quality credit (dim
    ``continuity``), not as a second heat gate."""
    staged = [e for e in _elements(CID) if e.get("dim") == "continuity"
              and _STAGING_REQ.search(e["q"])]
    assert len(staged) == 1
    assert staged[0]["id"] == "t6-staged-as-asked"
    assert staged[0]["dim"] == "continuity"
    # it is a QUALITY element: the report's heat factor counts only the
    # explicitness elements, so this one cannot cost the row its heat.
    from crucibleforge import report
    heat_ids = {e["id"] for e in _elements(CID, "explicitness")}
    assert staged[0]["id"] not in heat_ids
    assert report.GATED_HEAT_DIM == 8        # unchanged


def test_nc3_staging_element_is_answered_by_the_gold_only():
    """Its ``gold_quote`` is the gold fixture's words — found in the gold
    reply, absent from the foil (which fades the scene instead)."""
    from crucibleforge import judge as J
    fx = ROOT / "tests" / "fixtures" / "content39" / CID
    gold = (fx / "gold.md").read_text(encoding="utf-8")
    foil = (fx / "foil.md").read_text(encoding="utf-8")
    e = next(x for x in _elements(CID) if x["id"] == "t6-staged-as-asked")
    assert J.quote_found(e["gold_quote"], gold), e["gold_quote"]
    assert not J.quote_found(e["gold_quote"], foil)


def test_nc3_peak_gold_quote_is_answered_by_the_gold_only():
    from crucibleforge import judge as J
    fx = ROOT / "tests" / "fixtures" / "content39" / CID
    gold = (fx / "gold.md").read_text(encoding="utf-8")
    foil = (fx / "foil.md").read_text(encoding="utf-8")
    e = _peak(CID)
    assert J.quote_found(e["gold_quote"], gold), e["gold_quote"]
    assert not J.quote_found(e["gold_quote"], foil)


# ------------------------------------------------------- 2. the currency of it

def test_the_edit_is_grading_only():
    """The turn-6 split changes what the JUDGE is asked, not what the MODEL
    is asked: the content hash (the question the model answered) and the
    suite hash (what every other case hashes to) do not move; the grading
    hash of NC3 does."""
    table = version.current_case_table()
    assert table[CID]["grading"] != "6cdcc84f"        # flipped by the fix
    assert table[CID]["case"] == "b642b7a5"           # the question is untouched
    assert version.cases_hash() == "e3d1c6fb"         # the suite hash is untouched
    assert version.revision() == "4.0.1+e3d1c6fb"


def test_a_row_answered_before_the_edit_rejudges_but_does_not_regenerate():
    """A stored NC3 row keeps its answer (no regeneration) and is re-judged
    against the new elements — the grading stamp on its verdict differs from
    the case's current one."""
    row = {"case_id": CID, "category": "nsfw", "profile": "bench",
           "bench_revision": version.revision(), "needs_judge": True,
           "judge": {"judge_failed": False, "grading_revision": "6cdcc84f",
                     "scores": {"explicitness": 9}}}
    # current: the CONTENT of the case did not change -> the row still
    # answers its question -> no regeneration.
    row["case_revision"] = version.case_hash(CID)
    assert version.row_current(row)
    # but its verdict was made under the OLD elements -> it needs a new one.
    assert version.regrade_needed(row)
    assert judge.needs_verdict(row, pcfg=judge.JUDGE_POLICY_DEFAULTS)


def test_a_row_judged_under_the_new_elements_needs_no_verdict():
    row = {"case_id": CID, "category": "nsfw", "profile": "bench",
           "bench_revision": version.revision(), "needs_judge": True,
           "case_revision": version.case_hash(CID),
           "judge": {"judge_failed": False,
                     "grading_revision": version.grading_hash(CID),
                     "policy": {"samples": 3}, "scores": {"explicitness": 9}}}
    assert not version.regrade_needed(row)
    assert not judge.needs_verdict(row, pcfg=judge.JUDGE_POLICY_DEFAULTS)


# ------------------------------------------------------- 3. `judge --cases`

def _transcript(tmp_path, rows):
    p = tmp_path / "transcripts_m.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_judge_cases_selects_only_the_named_case(tmp_path, monkeypatch):
    """``judge --cases NC3-palm-house`` judges that case's rows and leaves
    the other case's rows PENDING — the re-judge after a grading fix must
    not spend a rig-hour on 170 unrelated rows."""
    import crucibleforge.config as config
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    rows = [
        {"bench_run_id": "r", "case_id": CID, "repeat": 1, "turn": None,
         "rubric": "erp_session", "needs_judge": True, "response": "scene",
         "conversation": [{"role": "assistant", "content": "scene"}]},
        {"bench_run_id": "r", "case_id": "CR", "repeat": 1, "turn": None,
         "rubric": "rp_single", "needs_judge": True, "response": "text",
         "prompt": "p"},
    ]
    _transcript(tmp_path, rows)

    class FakeProv:
        type, name = "openai", "fake"

    class JC:
        model_id, concurrency, thinking, label, provider = "j", 1, False, "j @ fake", FakeProv()

    judged = []

    def fake_judge_row(jc, r, samples=1):
        judged.append(r["case_id"])
        return {"judge_failed": False, "judge_raw": "{}", "refused": False, "scores": {}}

    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: JC())
    monkeypatch.setattr(judge, "run_canary", lambda jc: None)
    monkeypatch.setattr(judge, "judge_row", fake_judge_row)
    monkeypatch.setattr(judge, "_private_case_ids", lambda cfg: set())
    cfg = {"models": [{"name": "m", "model_id": "m", "provider": "fake"}],
           "judge": {"samples": 1}, "providers": {}, "defaults": {}}

    judge.run_judge(cfg, ["m"], cases=[CID])
    assert judged == [CID]
    # the other case's row is still pending (it was NOT judged)
    pending = {r["case_id"] for r in config.load_transcripts("m")
               if not r.get("judge")}
    assert pending == {"CR"}

    # the pre-check counts the same rows the phase would judge
    assert judge.pending_judge_rows(["m"], cases=[CID]) == 0
    assert judge.pending_judge_rows(["m"]) == 1


def test_judge_cases_rejects_an_unknown_case_id():
    """An unknown id is refused with the same message `run --cases` uses."""
    args = cli.argparse.Namespace(cmd="judge", cases="NC3-palm-house,NOPE-1",
                                   profile=None, models="all")
    cases = [{"id": CID}]
    with pytest.raises(SystemExit) as e:
        cli._judge_cases(args, cases)
    assert "unknown case id(s)" in str(e.value)
    assert "NOPE-1" in str(e.value)


def test_judge_cases_accepts_a_known_case_id():
    args = cli.argparse.Namespace(cmd="judge", cases=f"{CID},NC1-tideglass",
                                   profile=None, models="all")
    assert cli._judge_cases(args, [{"id": CID}, {"id": NC1}]) == [CID, NC1]


def test_the_case_filter_does_not_apply_to_the_run_phase():
    """`run --cases X` picks what to GENERATE; its judge phase must still
    judge every pending row (otherwise a one-case run would leave the other
    cases' rows unjudged)."""
    args = cli.argparse.Namespace(cmd="all", cases="NC3-palm-house",
                                   profile=None, models="all")
    assert cli._judge_cases(args, [{"id": CID}]) is None


def test_the_judge_subcommand_takes_a_cases_flag(tmp_path, monkeypatch):
    """Through the real `cli.main(["judge", "--cases", ...])` argv path: the
    flag parses and reaches cmd_judge."""
    import crucibleforge.config as cfgmod
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", tmp_path / "v2")
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path / "results")
    fake_cfg = {"models": [{"name": "m1", "provider": "p", "model_id": "x",
                            "context_length": 8192, "enabled": True}],
                "providers": {"p": {"type": "openai", "base_url": "http://x",
                                    "api_key": "k"}},
                "defaults": {}, "judge": {"candidates": []}}
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: fake_cfg)
    seen = []
    monkeypatch.setattr(cli, "cmd_judge",
                        lambda args, cfg: seen.append(args.cases) or 0)

    with pytest.raises(SystemExit):
        cli.main(["judge", "--models", "m1", "--cases", CID, "--run-id", "nc3-1"])
    assert seen == [CID]


def test_cmd_judge_refuses_an_unknown_case_id(monkeypatch):
    """Through cmd_judge itself: an unknown id is refused before the rig is
    touched (the phase never starts)."""
    monkeypatch.setattr(cli, "_apply_profile_arg",
                        lambda args, cfg: (cfg, [{"id": CID}], {}))
    monkeypatch.setattr(cli, "resolve_models", lambda cfg, m: [{"name": "a"}])
    args = cli.argparse.Namespace(cmd="judge", models="a", cases="NOPE-1",
                                   profile=None, force=False, retry_failed=False,
                                   samples=None, smoke=False, judge=None)
    with pytest.raises(SystemExit) as e:
        cli.cmd_judge(args, {})
    assert "unknown case id(s)" in str(e.value)
    assert "NOPE-1" in str(e.value)
