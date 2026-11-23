"""Stamp-2 batch G fix pass (REVIEW-STAMP2G-45-OPUS): M1, S1-S5, (a), (c), (d).

M1  the C6 removal is in force before any read is planned or bought (fresh board and resumed board); read45 /
    read_flags45 refuse a hosted pick while the C6 block is absent or of the old kind; --dry-run shows the pick.
S1  C6 on a board with no reader set known: "not determinable", nothing removed.
S2  before / after verdicts by the pick (before = the graded readers, GPT in; after = R1's pick), moved counts split
    into reader left / reader entered / both.
S3  only the adult page's keys may be missing from a run; a Code or Chat key missing stays None.
S4  the sanity block (plan B4) and the release STOP on it (plan C4) with --accept-sanity per entry.
S5  a family left without two readers is pending: read45 goes on, release45 records done-with-pending.
(a) an admitted R-Q / R-M / R-O (the profile's `set`, ahead of the hosted readers) needs a lead ruling.
(c) the margin legend says fix tasks are pooled while long scenes stay per scene.
(d) the removed reader is stamped on the board page (NSFW+ lines and "What is held back and why").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from crucibleforge import classing45 as C, judge, profiles

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]
#: Part 1 of the one build pass (owner 2026-11-21, INTENT-45 "No second reader, no hosted grader, no hosted checker"):
#: these tests pinned the retired multi-reader pick (hosted fallback readers, a third reader, C6 removal, the
#: admitted-pair report). One local judge now classes every unit (tests/test_judge45_part1_20261121.py).
RETIRED_PART1 = "retired by Part 1 (one local judge; the multi-reader pick is out of the scoring path)"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
FIX = ROOT / "tests" / "fixtures" / "stamp2g_runner_rows_20261121.json"
SHA = C.load_prompt({})[1]
NAMED = C.named_readers()
HOSTED = ("deepseek-v4-flash", "RH-kimi", "RH-gpt")
GPT = {"name": "RH-gpt", "model": NAMED["RH-gpt"]["model_id"], "family": "openai"}
DS, HER = "deepseek-flash", "qwen3.8-27b-ultra-heretic-nvfp4"


def _c1(*roles):
    return [{"model": NAMED[r]["model_id"], "family": NAMED[r].get("family"), "prompt_sha": SHA, "status": "pass"}
            for r in roles]


def _c1_block(*roles):
    return {"per_reader": {r: c for r, c in zip(roles, _c1(*roles))}, "status": "pass"}


@pytest.fixture
def nocases(monkeypatch):
    monkeypatch.setattr(judge, "_load_case_fields", lambda: None)
    monkeypatch.setattr(judge, "_CASE_FIELDS", {}, raising=False)


def _rows():
    return json.loads(FIX.read_text(encoding="utf-8"))["rows"]


def _folder(base: Path, name: str, label: str, classing: dict | None = None) -> Path:
    f = base / name
    f.mkdir(parents=True)
    (f / f"transcripts_{label}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in _rows()), encoding="utf-8")
    (f / f"meta_{label}.json").write_text("{}", encoding="utf-8")
    if classing is not None:
        (f / "marks45.json").write_text(json.dumps({"classing": classing}), encoding="utf-8")
    return f


def _gpt_refusing_store(folder: Path, label: str) -> int:
    """GPT read every current unit of a deepseek folder as the pair's second reader and refused each one."""
    units = C.current_units(folder)
    kimi = C.spec_from(NAMED["RH-kimi"], name="RH-kimi", hosted=True)
    gpt = C.spec_from(NAMED["RH-gpt"], name="RH-gpt", hosted=True)
    st = C.ReaderStore(folder / "readers45" / f"{label}.jsonl")
    for u in units:
        st.put(u["sha"], kimi, SHA, {"cls": "SOFTENED", "quote_ok": True, "reason": None, "quote": ""},
               {"model": label, "unit_id": u["unit_id"], "role": 0})
        st.put(u["sha"], gpt, SHA, {"cls": C.UNREAD, "quote_ok": False, "reason": "filtered", "quote": ""},
               {"model": label, "unit_id": u["unit_id"], "role": 1})
    return len(units)


OLD_C6 = {"status": "fail", "share": {"RH-gpt": 0.0847}, "reason": "RH-gpt refused on 8.47% of units"}


# ================================================================ M1: removal in force before any read
@pytest.mark.skip(reason=RETIRED_PART1)
def test_read45_refuses_a_hosted_pick_while_c6_is_of_the_old_kind(tmp_path, nocases, capsys):
    import read45
    f = _folder(tmp_path, "her", HER, {"C1_constructed": _c1_block(*HOSTED), "C6_reader_refusal": OLD_C6})
    rc = read45.main(["--results", str(f), "--dry-run"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "b-c6" in err and "check_classing_marks.py" in err and "no reader was called" in err
    # absent block: the same refusal
    (f / "marks45.json").write_text(json.dumps({"classing": {"C1_constructed": _c1_block(*HOSTED)}}), encoding="utf-8")
    assert read45.main(["--results", str(f), "--dry-run"]) == 2


@pytest.mark.skip(reason=RETIRED_PART1)
def test_read_flags45_refuses_a_hosted_pick_while_c6_is_of_the_old_kind(tmp_path, nocases, capsys):
    import read_flags45
    f = _folder(tmp_path, "her", HER, {"C1_constructed": _c1_block(*HOSTED), "C6_reader_refusal": OLD_C6})
    rc = read_flags45.main(["--results", str(f), "--dry-run"])
    assert rc == 2 and "b-c6" in capsys.readouterr().err


@pytest.mark.skip(reason=RETIRED_PART1)
def test_combined_store_removes_gpt_from_the_folders_alone(tmp_path, nocases):
    """A fresh board: C6 from the folders' own stores, before any read; the arithmetic counts real unmade reads."""
    a = _folder(tmp_path, "ds", DS)
    b = _folder(tmp_path, "her", HER)
    n = _gpt_refusing_store(a, DS)
    prof = profiles.load_profile("bench45")
    marks = json.loads(C.MARKS_PATH.read_text(encoding="utf-8"))
    blk = C.store_marks([a, b], prof, marks, c1=_c1(*HOSTED))["C6_reader_refusal"]
    assert blk["rule"] == C.C6_RULE and blk["status"] == "applied"
    assert [r["name"] for r in blk["removed"]] == ["RH-gpt"]
    assert blk["reads"]["RH-gpt"] == n and blk["refusals"]["RH-gpt"] == n
    # GPT is the third reader of the heretic units (its pair not read yet): each one is a read it could still make
    assert blk["not_yet_read"]["RH-gpt"] == len(C.current_units(b))
    assert sorted(blk["affected"]) == sorted([DS, HER])


def _inproc_runner(calls: list, outs: list):
    """The release's commands run in this process; a read call is always a dry run (no reader is ever called)."""
    import check_classing_marks as CCM
    import read45

    def run(cmd, env, log):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        name = Path(cmd[1]).name if len(cmd) > 1 else ""
        if name == "check_classing_marks.py":
            return CCM.main(cmd[2:])
        if name == "read45.py":
            args = cmd[2:] + ([] if "--dry-run" in cmd else ["--dry-run"])
            rc = read45.main(args)
            outs.append((cmd, rc))
            return rc
        return 0
    return run


@pytest.mark.skip(reason=RETIRED_PART1)
def test_release_fixes_c6_before_any_read_on_a_fresh_board(tmp_path, nocases, monkeypatch, capsys):
    import release45 as R
    monkeypatch.setattr(R.Release, "preflight_env", lambda self: None)
    cls = {"C1_constructed": _c1_block(*HOSTED), "C6_reader_refusal": OLD_C6}
    a = _folder(tmp_path, "ds", DS, cls)
    b = _folder(tmp_path, "her", HER, cls)
    _gpt_refusing_store(a, DS)
    calls, outs = [], []
    board = tmp_path / "board45"
    rc = R.main(["--folders", str(a), str(b), "--board", str(board), "--only", "b-c6,b-classing"],
                runner=_inproc_runner(calls, outs))
    text = capsys.readouterr()
    assert rc == 0, text
    names = [Path(c[1]).name for c in calls]
    assert names.index("check_classing_marks.py") < names.index("read45.py")
    c6 = json.loads((board / "marks45.json").read_text(encoding="utf-8"))["classing"]["C6_reader_refusal"]
    assert c6["rule"] == C.C6_RULE and [r["name"] for r in c6["removed"]] == ["RH-gpt"]
    for f in (a, b):                                       # the block reached every folder before its read
        assert json.loads((f / "marks45.json").read_text(encoding="utf-8"))["classing"]["C6_reader_refusal"]["rule"] == C.C6_RULE
    picks = [ln for ln in text.out.splitlines() if ln.startswith("read45:") and "readers [" in ln]
    assert picks and not any("RH-gpt" in ln for ln in picks)
    assert "deepseek-v4-flash', 'RH-kimi'" in text.out
    # S5: the deepseek folder is pending, the release went on and says so in its marker
    m = json.loads((a / ".release45" / "b-classing.json").read_text(encoding="utf-8"))
    assert m["pending"] and "C6 removed RH-gpt" in " ".join(m["pending"].values())
    assert "DONE-PENDING b-classing" in (a / "release45.log").read_text(encoding="utf-8")
    mb = json.loads((b / ".release45" / "b-classing.json").read_text(encoding="utf-8"))
    assert not mb.get("pending")


@pytest.mark.skip(reason=RETIRED_PART1)
def test_release_dry_run_shows_the_pick_without_gpt(tmp_path, nocases, monkeypatch, capsys):
    import release45 as R
    cls = {"C1_constructed": _c1_block(*HOSTED), "C6_reader_refusal": OLD_C6}
    a = _folder(tmp_path, "ds", DS, cls)
    b = _folder(tmp_path, "her", HER, cls)
    _gpt_refusing_store(a, DS)
    rc = R.main(["--folders", str(a), str(b), "--board", str(tmp_path / "board45"), "--dry-run",
                 "--only", "b-c6,b-classing"])
    out = capsys.readouterr().out
    assert rc == 0
    pick = [ln for ln in out.splitlines() if "[b-classing]" in ln]
    assert any("pending readers" in ln and DS in ln for ln in pick)
    assert any("deepseek-v4-flash, RH-kimi" in ln for ln in pick)
    assert not any("RH-gpt" in ln and "removed" not in ln for ln in pick)
    assert not (tmp_path / "board45").exists()


# ================================================================ S1: no reader set known
def _l(model, sha, name, role, cls, reason=None):
    fam = {"ds": "deepseek", "kimi": "moonshot", "gpt": "openai", "loc": "clover"}[name]
    return {"model": model, "unit": sha, "reader": name, "role": role, "prompt_sha": "x",
            "vote": {"cls": cls, "quote_ok": True, "reason": reason, "quote": "q",
                     "stamp": {"model": name + "-id", "family": fam, "prompt_sha": "x"}}}


def _spec(name):
    fam = {"ds": "deepseek", "kimi": "moonshot", "gpt": "openai", "loc": "clover"}[name]
    return C.ReaderSpec(name=name, provider="p", model_id=name + "-id", family=fam, hosted=name != "loc")


def _u(model, sha, card="c", variant="write"):
    return {"model": model, "sha": sha, "card": card, "seed_set": 1, "unit_id": f"{card}|s1|r1|{sha}",
            "variant": variant}


def test_c6_is_not_determinable_without_the_reader_set():
    units = [_u("d", f"d{i}") for i in range(10)]
    lines = [_l("d", f"d{i}", "kimi", 0, "SOFTENED") for i in range(10)]
    lines += [_l("d", f"d{i}", "gpt", 1, C.UNREAD, reason="filtered") for i in range(3)]
    for rbm in (None, {"d": None}, {}):
        b = C.marks_from_store(lines, {"marks": {}}, units=units, readers_by_model=rbm)["C6_reader_refusal"]
        assert b["status"] == "undetermined" and b["removed"] == []
        assert "not determinable" in b["arithmetic"]["gpt"]
        assert b["not_yet_read"]["gpt"] is None


# ================================================================ S2: before / after by the pick
def test_moved_counts_compare_the_graded_readers_with_r1s_pick():
    units = [_u("q", "left"), _u("q", "entered"), _u("q", "same")]
    lines = [
        # GPT broke the tie; after R1 the local third reads it the other way (written later, same stored role)
        _l("q", "left", "ds", 0, "EXPLICIT"), _l("q", "left", "kimi", 1, "SOFTENED"),
        _l("q", "left", "gpt", 2, "EXPLICIT"), _l("q", "left", "loc", 2, "SOFTENED"),
        # GPT never read it; the local third settles it
        _l("q", "entered", "ds", 0, "EXPLICIT"), _l("q", "entered", "kimi", 1, "SOFTENED"),
        _l("q", "entered", "loc", 2, "EXPLICIT"),
        _l("q", "same", "ds", 0, "EXPLICIT"), _l("q", "same", "kimi", 1, "EXPLICIT"),
        _l("q", "same", "gpt", 2, C.UNREAD, reason="no_json"),
    ]
    before = {"q": [_spec("ds"), _spec("kimi"), _spec("gpt")]}

    def pick(label, removed):
        return [_spec("ds"), _spec("kimi"), _spec("loc")] if removed else before[label]
    r = C.marks_from_store(lines, {"marks": {}}, units=units, readers_by_model=before, pick=pick)
    c6 = r["C6_reader_refusal"]
    assert c6["status"] == "applied"
    mv = c6["moved"]["q"]
    assert mv["of"] == 3 and mv["unchanged"] == 1
    assert mv["reader_left"] == 1 and mv["reader_entered"] == 1 and mv["left_and_entered"] == 0
    assert mv["by"] == {"EXPLICIT->SOFTENED": 1, "SOFTENED->EXPLICIT": 1}
    # C5 reads the after pick: the local third counts on both disagreeing units
    assert r["C5_third_reader_load"]["share"]["q"] == round(2 / 3, 4)


# ================================================================ S3: only the adult page's keys may be missing
def test_only_adult_keys_may_be_missing_from_a_run(tmp_path):
    import check_marks45 as CM
    from crucibleforge import marksinput45 as MI
    assert MI.adult_key("nsfw/erotica") and MI.adult_key("unc/knowledge") and MI.adult_key("headline/nsfw")
    assert MI.adult_key("headline/uncensored") and not MI.adult_key("chat/everyday")
    assert not MI.adult_key("code/fixes")
    s = {"points": 1.0, "partial": 1.0, "max": 4.0, "margin": 0.1, "complete": True, "reason": None}
    r1 = CM.Run(tmp_path, "m", {"segments": {"code/writes": s, "nsfw/erotica": s}})
    r2 = CM.Run(tmp_path, "m", {"segments": {}})
    M = CM.Models([r1, r2], {})
    assert M.value("m", "code/writes") is None
    assert "code/writes" in M.why_missing("m", "code/writes")
    v = M.value("m", "nsfw/erotica")
    assert v["n"] == 1 and v["runs_used"] == 1 and v["runs_total"] == 2


# ================================================================ S4: the sanity block and its STOP
def test_sanity_entries():
    import check_sanity45 as S
    v = [("a", "session", "OFF_TOPIC"), ("a", "session", "OFF_TOPIC"), ("b", "session", "OFF_TOPIC"),
         ("a", "write", "EXPLICIT"), ("b", "write", "SOFTENED")]
    e = S.same_class_entries(v)
    assert [x["code"] for x in e] == ["same-class:session"] and "OFF_TOPIC" in e[0]["detail"]
    assert S.same_class_entries([("a", "session", "OFF_TOPIC")]) == []          # one model is no evidence
    z = S.zero_entries({"a": {"nsfw/roleplay": 0.0, "nsfw/erotica": 1.0, "unc/willingness/u4": 0.0},
                        "b": {"nsfw/roleplay": 0.0, "nsfw/erotica": 0.0, "unc/willingness/u4": 0.0}})
    assert sorted(x["code"] for x in z) == ["zero:nsfw/roleplay", "zero:unc/willingness/u4"]
    assert S.zero_entries({"a": {"nsfw/roleplay": 0.0}}) == []


def test_sanity_lists_collisions_and_empty_region_reads(tmp_path, nocases, monkeypatch):
    import check_sanity45 as S
    f = _folder(tmp_path, "b", DS)
    # a stored read of an empty region: the sha of (ask, "") for a classed unit whose region is empty
    units = C.units_from_rows(C.board_rows(f / f"transcripts_{DS}.jsonl"))
    u = next(x for x in units if x["ask"] is not None)
    st = C.ReaderStore(f / "readers45" / f"{DS}.jsonl")
    st.put(C.unit_sha(u["ask"], ""), C.spec_from(NAMED["RH-kimi"], name="RH-kimi", hosted=True), SHA,
           {"cls": "OFF_TOPIC", "quote_ok": True, "reason": None, "quote": ""}, {"model": DS, "unit_id": u["unit_id"]})
    monkeypatch.setattr(C, "unit_pre_read", lambda x: "empty" if x["unit_id"] == u["unit_id"] else None)
    e = S.store_entries(f)
    assert [x["code"] for x in e] == [f"empty-read:{DS}:{u['unit_id']}"]

    def boom(rows, **kw):
        raise C.DuplicateUnitId("two units share the id c|s1|r1|run")
    monkeypatch.setattr(C, "units_from_rows", boom)
    assert [x["code"] for x in S.store_entries(f)] == [f"id-collision:{DS}"]


def _sanity_runner(entries):
    import test_release45 as TR

    def run(cmd, env, log):
        cmd = [str(c) for c in cmd]
        if len(cmd) > 1 and Path(cmd[1]).name == "check_sanity45.py":
            p = Path(cmd[cmd.index("--results") + 1]) / "marks45.json"
            cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
            cur["sanity"] = {"entries": entries}
            p.write_text(json.dumps(cur), encoding="utf-8")
            return 1 if entries else 0
        TR.fake_verdict(cmd)
        return 0
    return run


def test_release_stops_on_a_sanity_entry_until_each_is_accepted(tmp_path, monkeypatch, capsys):
    import release45 as R
    import test_release45 as TR
    monkeypatch.setattr(R.Release, "preflight_env", lambda self: None)
    a = TR.mkfolder(tmp_path, "r1", "model-a")
    ent = [{"code": "zero:nsfw/roleplay", "detail": "0.0 for every model"},
           {"code": "same-class:session", "detail": "every unit OFF_TOPIC"}]
    argv = ["--folders", str(a), "--board", str(tmp_path / "board45"), "--only", "c-sanity"]
    rc = R.main(argv, runner=_sanity_runner(ent))
    out = capsys.readouterr().out
    assert rc == 7 and "zero:nsfw/roleplay" in out and "same-class:session" in out and "--accept-sanity" in out
    rc = R.main(argv + ["--accept-sanity", "zero:nsfw/roleplay"], runner=_sanity_runner(ent))
    assert rc == 7 and "same-class:session" in capsys.readouterr().out
    rc = R.main(argv + ["--accept-sanity", "zero:nsfw/roleplay", "--accept-sanity", "same-class:session"],
                runner=_sanity_runner(ent))
    assert rc == 0
    log = (tmp_path / "board45" / "release45.log").read_text(encoding="utf-8")
    assert "ACCEPTED sanity zero:nsfw/roleplay by " in log and "ACCEPTED sanity same-class:session by " in log
    assert "c-sanity" in R.STEPS and R.STEPS.index("c-sanity") < R.STEPS.index("d-report")
    assert R.STEPS.index("b-c6") < R.STEPS.index("b-classing")


# ================================================================ (a) the `set` roles need a lead ruling
@pytest.mark.skip(reason=RETIRED_PART1)
def test_an_admitted_set_reader_needs_a_lead_ruling():
    prof = profiles.load_profile("bench45")
    with pytest.raises(C.ReaderOrderUnruled, match="lead ruling"):
        C.pick_readers("minimax", prof, named=NAMED, c1=_c1("R-M", *HOSTED), prompt_sha=SHA)
    # not admitted: the order question does not arise
    assert [r.name for r in C.pick_readers("minimax", prof, named=NAMED, c1=_c1(*HOSTED), prompt_sha=SHA)] == \
        list(HOSTED)


# ================================================================ (c) the margin legend
def test_margin_legend_says_fixes_are_pooled_and_scenes_are_not():
    from crucibleforge import report
    t = " ".join(report.seg45_legend([], {}))
    assert "fix tasks are pooled" in t and "per scene" in t


# ================================================================ (d) the removed reader on the board page
RR = {"removed": [GPT], "reason": "C6: ...", "counts": {"RH-gpt": [5, 59]},
      "moved": {"of": 40, "changed": 2, "to_pending": 0}}


def _stats():
    num = {"label": "NSFW", "of": 10, "value": None, "partial": 0.0,
           "segments": {"erotica": {"label": "Erotica", "max": 4.0, "value": 1.0}}}
    unc = {**num, "label": "Uncensored"}
    a = {"nsfw_plus": {"state": "pending", "numbers": {"nsfw": num, "uncensored": unc}, "readers_removed": RR}}
    b = {"nsfw_plus": {"state": "pending", "numbers": {"nsfw": num, "uncensored": unc}}}
    return {"affected": a, "other": b}


@pytest.mark.skip(reason=RETIRED_PART1)
def test_removed_reader_is_stamped_on_the_nsfw_lines():
    from crucibleforge import report
    want = "one reader was removed for this run: it refused more than 2% of its reads (5 of 59)"
    st = _stats()
    md = "\n".join(report.nsfw_plus_md(["affected", "other"], st))
    assert md.count(want) == 1
    blk = md.split("**other**")
    assert want in blk[0] and want not in blk[1]
    html = report.nsfw_plus_html(["affected", "other"], st)
    assert html.count(want) == 1


def test_removed_reader_is_in_held_back_by_model():
    from crucibleforge import display45 as D
    st = _stats()
    notes = D._model_notes(st["affected"], None, np_in=True)
    assert any("one reader was removed for this run" in n.lower() for n in notes)
    assert not any("removed" in n for n in D._model_notes(st["other"], None, np_in=True))


def test_pending_readers_is_said_in_plain_words():
    from crucibleforge import report
    t = report._np_plain("classing readers: C6 removed RH-gpt; fewer than two readers of other families for a "
                         "deepseek row (have ['RH-kimi']; no other family named) | Erotica pending")
    assert "pending readers" in t and "not named yet" not in t


@pytest.mark.skip(reason=RETIRED_PART1)
def test_a_model_waiting_for_readers_says_pending_readers_on_its_lines(tmp_path, nocases):
    """S5 on the board page: the marks overwrite a segment's pending reason, so the readers' state has its own line."""
    from crucibleforge import nsfwplus45 as N, report
    f = _folder(tmp_path, "ds", DS, {"C1_constructed": _c1_block(*HOSTED), "C6_reader_refusal": {
        "status": "applied", "rule": C.C6_RULE, "removed": [GPT], "affected": [DS], "reads": {"RH-gpt": 59},
        "refusals": {"RH-gpt": 5}}})
    blk = N.report_block(_rows(), DS, f, profiles.load_profile("bench45"))
    assert "C6 removed RH-gpt" in blk["readers_pending"]
    t = report.np_readers_removed({"nsfw_plus": blk})
    assert "pending readers" in t and "(5 of 59)" in t
