"""rc4-main standard board, ``pending_scaling: none`` (lead 2026-11-15, D's find on integration cfc5aca): nothing
is ever scaled up and nothing blanks a headline. A part that is not run, not measured, pending the judge or not
validated adds no points and is named; the score prints as measured points of the measured maximum.

Real rows: the 8B (deepseek-r1-0528-8b) from D's pooled rc4 board (d-work/real2/results-rc2-ds8b with D2's tool rows),
its stats as ``stats_from_tier_rows`` builds them (tests/fixtures/rc4_main_ds8b_stats_20261115.json). Its Coding has
two errored tool jobs (TZ19 r1, r2: context_exceeded) and a Predict part with 4 of 5 calls cut at the ceiling; its
Chat has no long-scene rows. Before the fix both cells printed "-" (the missing-row policy blanked the whole score)."""
import json
import re
from pathlib import Path

import pytest

from crucibleforge import headline, profiles, report

FX = Path(__file__).parent / "fixtures" / "rc4_main_ds8b_stats_20261115.json"


@pytest.fixture(scope="module")
def prof():
    mp = pytest.MonkeyPatch()
    mp.setenv("CF_COMPOSITION", "rc4-main")
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    yield p
    mp.undo()


@pytest.fixture(autouse=True)
def _bench45_active(prof):
    # conftest resets the active profile after every test; a later call that activates the default profile (4.1's
    # archive tree) would leave ladder.LADDER_DIR on archive/4.1.0 for the next module. Keep bench45 active here.
    profiles.set_active("bench45")
    profiles.activate_cases_root(prof)
    yield


@pytest.fixture(scope="module")
def s8b():
    d = json.loads(FX.read_text(encoding="utf-8"))
    return d["stats"], d["scoring"]


@pytest.fixture(scope="module")
def card(prof, s8b):
    s, sc = s8b
    profiles.set_active("bench45")     # a module fixture is built before the autouse one below runs
    return report.points_scorecard(dict(s), sc, prof)


def _defs(card, key):
    return next(d for d in card["score_defs"] if d["key"] == key)


def _named(pend):
    return sum(x["points"] for x in {x["part"]: x for x in pend}.values())


def test_rc4_main_is_no_scale(prof):
    assert report.no_scaling(prof)


def test_8b_coding_is_its_measured_points_never_a_dash(card):
    lab = card["labels"]["work"]
    assert not lab.startswith("-"), lab
    parts = card["parts"]["work"]
    # Implied and Trace ran whole; Tools has two errored jobs (its other rows never stand in for them) and
    # Predict ran out on 4 of 5 calls: both add nothing
    assert parts["c5_tools"] is None and parts["c2_predict"] is None
    want = parts["c1_implied"] + parts["c2_trace"]
    assert card["work"] == pytest.approx(want)
    assert card["measured_max"]["work"] == pytest.approx(5 + 1.25)
    assert lab.startswith(f"{want:.1f} of 6.25"), lab
    pend = {x["part"]: x for x in card["pending_points"]["work"]}
    # step 2 (2026-11-21): the seeded tool tasks' part (max 0, chain work/c5_toolgen; this fixture predates it) is
    # pending too and carries no points
    assert set(pend) == {"c5_tools", "c2_predict", "c5_toolgen"}
    assert "errored" in pend["c5_tools"]["why"] and pend["c2_predict"]["why"] == "not measured"
    assert "2.5 pts pending a re-run of 2 errored jobs" in lab and "1.25 pts not measured" in lab


def test_8b_chat_is_its_measured_points_never_a_dash(card):
    lab = card["labels"]["chat"]
    assert not lab.startswith("-"), lab
    parts = card["parts"]["chat"]
    want = parts["h2_state"] + parts["h2_ledger"] + parts["h6_know"]
    assert card["chat"] == pytest.approx(want)
    assert card["measured_max"]["chat"] == pytest.approx(3.0)
    assert lab.startswith(f"{want:.1f} of 3"), lab
    # every pending point named once: RPS 4 + RPL 3 = 7 (real person left the test, its 2 went to role-play)
    assert _named(card["pending_points"]["chat"]) == pytest.approx(7.0)
    head = re.search(r"\((.*)\)", lab).group(1)
    assert sum(float(x) for x in re.findall(r"(\d+(?:\.\d+)?) pts?\b", head)) == pytest.approx(7.0), lab


def test_named_pending_points_never_exceed_the_unmeasured_maximum(card):
    for key in ("work", "chat"):
        mx = sum(p["weight"] for p in _defs(card, key)["parts"])
        assert _named(card["pending_points"][key]) == pytest.approx(mx - card["measured_max"][key])


def test_the_scorecard_cell_reads_the_same(card, prof):
    hl = headline.summarize(card, None, prof)
    cod = report.points_cell(hl, _defs(card, "work"))
    assert cod.startswith(f"{card['work']:.1f} of 6.25 ("), cod
    # fix round 2026-11-18 (R5): Tools and Predict print as "Tool rules" and "Traps"
    assert "Tool rules pending" in cod and "Traps not measured" in cod and "scaled" not in cod
    chat = report.points_cell(hl, _defs(card, "chat"))
    assert chat.startswith(f"{card['chat']:.1f} of 3 ("), chat
    assert "scaled" not in chat


def test_no_score_is_scaled_on_rc4_main(card):
    # the score IS the sum of its counted parts' points (share x max + its scored packs): never over a smaller base
    for key in ("work", "chat"):
        got = [v for v in card["parts"][key].values() if v is not None]
        assert card[_defs(card, key)["group"]] == pytest.approx(sum(got))


def test_the_public_measured_block_matches_the_cell(card, prof, s8b):
    s = dict(s8b[0])
    s["scorecard"] = card
    s["headline"] = headline.summarize(card, None, prof)
    m = report.points_measured(s)
    for key in ("work", "chat"):
        assert m[key]["points"] == pytest.approx(card[_defs(card, key)["group"]])
        assert m[key]["measured_max"] == pytest.approx(card["measured_max"][key])
        assert m[key]["points"] <= m[key]["measured_max"] + 1e-9
        assert m[key]["pending_points"] == pytest.approx(_named(card["pending_points"][key]))
    tools = next(x for x in m["work"]["pending"] if x["part"] == "c5_tools")
    assert "errored" in tools["reason"]


def test_the_margin_draw_never_scales_on_rc4_main(prof):
    # _add_question_noise moved a score by d x (t1 / standard): on a no-scale profile that factor is 1
    card = {"subscores": {"work": {"standard": 2.0}}, "t1": {"coding": 4.0}}
    v = {"work/c1_implied": 1.0, "work": 2.0, "overall": 5.0}

    class Z:
        @staticmethod
        def gauss(mu, sd):
            return mu + sd          # one sd up
    headline._add_question_noise(v, card, prof, {"work/c1_implied": 0.1}, Z)
    mx = headline.part_max(prof)["work/c1_implied"]
    assert v["work"] == pytest.approx(2.0 + 0.1 * mx)
    assert v["overall"] == pytest.approx(5.0 + 0.1 * mx)


def test_a_model_with_rows_missing_is_not_ranked_and_says_why(card, prof, s8b):
    assert card["total"] is None
    why = card["unranked_why"]
    # (step 2: the max-0 seeded-tool part reads its category's rows in the old tree, so the two errored Tools jobs
    # are named under it as well; its points are segments45's)
    assert why.startswith("not ranked: ") and "jobs to re-run (Tool rules, ToolGen)" in why, why
    # real person left the test (owner 2026-11-15): never named, never waited for
    # fix round 2026-11-18 (lane-t, hook e): no RPS unit of this fixture was ever attempted (12 units, 0 attempts), so
    # the part reads "not run" like RPL; "units still running" is for a part some of whose units ran
    assert "not run (RPS, RPL)" in why and "units still running" not in why and "Person" not in why, why
    # Predict (not measured) and the validation-only gaps never stop the ranking, so they are not named here
    assert "Predict" not in why
    hl = headline.summarize(card, None, prof)
    assert report.headline_cell(hl, "overall") == why
    # Coding and Chat still print their measured points in their own cells
    assert report.headline_cell(hl, "work") != "-" and report.headline_cell(hl, "chat") != "-"


def test_a_model_whose_only_gaps_are_validation_stays_ranked(prof, s8b, monkeypatch):
    # lane-j: a judged part counts once its second-reader marks pass (the sessions' do here; long scenes stay pending)
    from crucibleforge.agree_marks import marks_sha256
    monkeypatch.setattr(report, "judge_marks_result", lambda: {"marks_sha256": marks_sha256(),
                                                               "parts": {"chat/j_rpsession": {"pass": True}}})
    s = json.loads(json.dumps(s8b[0]))
    s["missing_jobs"], s["missing_cases"] = [], []
    for pk in ("chat/j_rpsession",):
        s["chain_stats"][pk].update(complete=True, share=0.5, mean=0.5)
    c = report.points_scorecard(s, s8b[1], prof)
    assert not ({"work", "chat"} & set(c["partial"])), c["partial"]
    # F4 (2026-11-19): this fixture's Predict is "not measured" (a counted standard part), so Overall is "x of y" and
    # the row is not ranked, and that is the ONLY reason named: the validation-only gap (RPL) never is
    assert c["total"] is None and c["unranked_why"] == "not ranked: not measured (Traps)", c.get("unranked_why")
    assert c["overall_measured"]["points"] == pytest.approx(c["work"] + c["chat"])
    # the validation-pending part (RPL) adds nothing. Role-play sessions counts since the ladder judge was validated
    # for 4.5.0 (lead 2026-11-15, test_rpladder_board_20261115): Chat = State + Tally + Know + RPS
    parts = c["parts"]["chat"]
    assert parts["j_rplong"] is None
    assert c["chat"] == pytest.approx(parts["h2_state"] + parts["h2_ledger"] + parts["h6_know"] + parts["j_rpsession"])
    assert [x["part"] for x in c["pending_points"]["chat"]] == ["j_rplong"]
    assert headline.summarize(c, None, prof).get("not_ranked") == "not ranked: not measured (Traps)"


def test_merging_twice_keeps_each_reason_and_its_words():
    pend = [{"part": "n7_taboo", "points": 1.0, "why": "the judge"},
            {"part": "n7_taboo", "points": 1.0, "why": "pending validation (machine checks)"}]
    once = report.merge_pending(pend)
    assert report.merge_pending(once) == once
    assert report.pending_points_text(once) == report.pending_points_text(pend) == (
        " (1 pt pending the judge and machine checks)")
