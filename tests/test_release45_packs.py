"""Stamp-2 item A: the second-reader release step for the Chat packs (b-packs, c-packmarks) and the reader store."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release45 as R  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_release45 import fake_verdict  # noqa: E402

from crucibleforge import agree as AG  # noqa: E402
from crucibleforge import packs45 as PK  # noqa: E402
from crucibleforge import rplong_mean as RM  # noqa: E402


@pytest.fixture(autouse=True)
def _no_env_preflight(monkeypatch):
    monkeypatch.setattr(R.Release, "preflight_env", lambda self: None)
    monkeypatch.setattr(R.Release, "fix_needed", lambda s, f: ([], []))
    monkeypatch.setattr(R.Release, "everyday_needed", lambda s, f: [])


def pack_row(case="LRV1-pilots-mess", rate=True, sha="sha1", resp="hello"):
    return {"case_id": case, "rubric": "rp_deep", "bench_run_id": "r", "repeat": 1, "response": resp,
            "judge": {"elements": {"results": [{"id": "a", "pass": rate}]}, "instrument": {"rubric_sha": sha}}}


def mkfolder(tmp: Path, name: str, label: str, rows: list[dict]) -> Path:
    f = tmp / name
    f.mkdir()
    (f / f"transcripts_{label}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (f / f"meta_{label}.json").write_text("{}", encoding="utf-8")
    return f


class Sim:
    def __init__(self, hosted=True, complete_after_agree=True, complete=False):
        self.cmds: list[list[str]] = []
        self.hosted, self.done, self.after = hosted, complete, complete_after_agree

    def runner(self, cmd, env, log):
        c = [str(x) for x in cmd]
        self.cmds.append(c)
        if "--parts" in c and "chat/packs45" in c and self.after:
            self.done = True
        fake_verdict(c)
        return 0

    def capturer(self, cmd, env):
        c = [str(x) for x in cmd]
        self.cmds.append(c)
        if "read_chat45.py" in c[1] and "--verify" in c:
            return (0, "m: 2 of 2 pack scene(s) have both readings\n") if self.done else \
                (1, "m: 0 of 2 pack scene(s) have both readings; unread: a\n")
        if "read_chat45.py" in c[1]:
            return 0, ("m: reader r1 (qwen" + (", hosted: needs --cap-usd" if self.hosted else "") +
                       "); run: crucibleforge agree --parts chat/packs45 --reader r1 --reader-thinking on f\n")
        return 0, ""

    def agree_cmds(self):
        return [c for c in self.cmds if "--parts" in c and "chat/packs45" in c and "read_chat45.py" not in c[1]]


def run(tmp, argv, sim):
    folders = sorted(p for p in tmp.iterdir() if p.is_dir() and not p.name.startswith("board"))
    return R.main(["--folders", *map(str, folders), "--board", str(tmp / "board45"), *argv],
                  runner=sim.runner, capturer=sim.capturer)


# ---- the steps exist, in the planned order
def test_steps_are_listed_in_order_and_the_docstring_names_them():
    s = list(R.STEPS)
    assert s.index("b-packs") == s.index("b-chat") + 1
    assert s.index("c-packmarks") == s.index("c-longscene") + 1
    assert "c-packmarks" in R.BOARD_STEPS and "b-packs" not in R.BOARD_STEPS
    assert "b-packs" in R.__doc__ and "c-packmarks" in R.__doc__ and "--pack-cap-usd" in R.__doc__


# ---- 6: a hosted reader needs the cap; a filled store makes no call
def test_b_packs_with_a_hosted_reader_and_no_cap_exits_2_before_any_call(tmp_path, capsys):
    mkfolder(tmp_path, "r1", "model-a", [pack_row()])
    sim = Sim(hosted=True)
    rc = run(tmp_path, ["--only", "b-packs"], sim)
    assert rc == 2 and "--pack-cap-usd" in capsys.readouterr().out
    assert sim.agree_cmds() == []
    assert not list(tmp_path.glob("r1/.release45/b-packs.json"))


def test_b_packs_with_the_cap_runs_the_reader_then_verifies(tmp_path):
    mkfolder(tmp_path, "r1", "model-a", [pack_row()])
    sim = Sim(hosted=True)
    rc = run(tmp_path, ["--only", "b-packs", "--pack-cap-usd", "2.5"], sim)
    assert rc == 0
    (cmd,) = sim.agree_cmds()
    assert cmd[cmd.index("--cap-usd") + 1] == "2.5"
    assert list(tmp_path.glob("r1/.release45/b-packs.json"))


def test_b_packs_is_not_done_while_a_pack_scene_is_unread(tmp_path, capsys):
    mkfolder(tmp_path, "r1", "model-a", [pack_row()])
    sim = Sim(hosted=True, complete_after_agree=False)
    rc = run(tmp_path, ["--only", "b-packs", "--pack-cap-usd", "2.5"], sim)
    assert rc == 3 and "incomplete" in capsys.readouterr().out
    assert not list(tmp_path.glob("r1/.release45/b-packs.json"))


def test_a_second_run_with_the_store_filled_calls_no_reader_and_needs_no_cap(tmp_path):
    mkfolder(tmp_path, "r1", "model-a", [pack_row()])
    sim = Sim(hosted=True, complete=True)
    rc = run(tmp_path, ["--only", "b-packs"], sim)         # no cap given: nothing to call, so nothing to refuse
    assert rc == 0 and sim.agree_cmds() == []


def test_a_folder_without_pack_rows_has_nothing_to_do_and_is_marked_done(tmp_path):
    mkfolder(tmp_path, "r1", "model-a", [{"case_id": "X-1", "repeat": 1}])
    sim = Sim(hosted=True)
    assert run(tmp_path, ["--only", "b-packs"], sim) == 0
    assert sim.cmds == [] and list(tmp_path.glob("r1/.release45/b-packs.json"))


def test_c_packmarks_runs_the_check_over_every_folder_and_anchor_and_spreads_the_block(tmp_path):
    mkfolder(tmp_path, "r1", "model-a", [pack_row()])
    a = mkfolder(tmp_path, "an", "claude-sonnet-x", [pack_row()])
    (tmp_path / "board45").mkdir()
    seen = []

    def runner(cmd, env, log):
        seen.append([str(c) for c in cmd])
        if Path(cmd[1]).name == "check_pack_marks45.py":
            (tmp_path / "board45" / "marks45.json").write_text(json.dumps({"chat_packs": {"x": 1}}), encoding="utf-8")
        return 0
    folders = [tmp_path / "r1"]
    rc = R.main(["--folders", *map(str, folders), "--board", str(tmp_path / "board45"), "--only", "c-packmarks",
                 "--anchors", str(a)], runner=runner, capturer=lambda c, e: (0, ""))
    assert rc == 0
    (cmd,) = [c for c in seen if Path(c[1]).name == "check_pack_marks45.py"]
    assert str(a) in cmd and str(tmp_path / "r1") in cmd and "--anchors" in cmd
    assert json.loads((tmp_path / "r1" / "marks45.json").read_text(encoding="utf-8"))["chat_packs"] == {"x": 1}


def test_dry_run_plans_both_steps(tmp_path, capsys):
    mkfolder(tmp_path, "r1", "model-a", [pack_row()])
    assert run(tmp_path, ["--dry-run"], Sim()) == 0
    out = capsys.readouterr().out
    # Part 1 (2026-11-21): the Chat pack second reader (b-packs) left the default order; it runs only by name.
    # Part 2 item 13: the pack marks are notes, out of the default order too (judge, assemble, report, accuracy)
    assert "[b-packs] r1" not in out and "[c-packmarks]" not in out and "[b-judge]" in out
    assert run(tmp_path, ["--dry-run", "--only", "b-packs,c-packmarks"], Sim()) == 0
    out = capsys.readouterr().out
    assert "[b-packs] r1" in out and "[c-packmarks]" in out


# ---- the reader store: resumable, row_sha keyed
class FakeJC:
    pass


def _patch_reader(monkeypatch):
    calls = []

    def regrade(jc, row, thinking=False):
        calls.append(row["case_id"])
        return {"elements": {"results": [{"id": "a", "pass": True}]}}
    monkeypatch.setattr(PK.AG, "regrade_long", regrade)
    monkeypatch.setattr(PK.AG, "long_record", lambda label, uid, row, v, case, key: {
        "part": AG.LONG_PART, "model": label, "unit": uid, "row_sha": AG.row_sha(row), "reader": key,
        "score_judge": 1.0, "score_reader": 1.0})
    return calls


def test_run_second_reader_second_run_makes_zero_calls_and_a_changed_row_is_re_read(tmp_path, monkeypatch):
    calls = _patch_reader(monkeypatch)
    rd = RM.ReaderSpec(id="r1", family="alibaba", thinking=True)
    rows = [pack_row("LRV1-pilots-mess"), pack_row("LCC1-quiet-hours")]
    kw = dict(jc=FakeJC(), results=tmp_path, label="m", cases={}, graded_family="minimax")
    PK.run_second_reader(rows, rd, **kw)
    assert len(calls) == 2
    PK.run_second_reader(rows, rd, **kw)
    assert len(calls) == 2                                        # a second run: zero reader calls
    rows2 = [pack_row("LRV1-pilots-mess", resp="changed text"), pack_row("LCC1-quiet-hours")]
    PK.run_second_reader(rows2, rd, **kw)
    assert calls[2:] == ["LRV1-pilots-mess"]                      # only the row whose sha differs


# ---- 7: pack reader records move no standard scene score
def test_pack_records_move_no_standard_scene_score(tmp_path, monkeypatch):
    _patch_reader(monkeypatch)
    rd = RM.ReaderSpec(id="r1", family="alibaba", thinking=True)
    std = pack_row("RV1-three-at-the-table")
    packs = [pack_row("LRV1-pilots-mess"), pack_row("LCC1-quiet-hours")]
    PK.run_second_reader(packs, rd, jc=FakeJC(), results=tmp_path, label="m", cases={}, graded_family="minimax")
    recs = list(RM.load_store(tmp_path, "m").values())
    assert recs
    before = RM.scene_scores([std], [], label="m", cases={}, graded_family="minimax", reader_for=rd)
    after = RM.scene_scores([std, *packs], recs, label="m", cases={}, graded_family="minimax", reader_for=rd)
    assert set(after) == {"r|RV1-three-at-the-table|1"}           # pack scenes are not standard scenes
    assert after == before


# ---- the agree command takes the packs part
def test_cli_agree_plan_counts_pack_units(tmp_path, capsys):
    from crucibleforge import cli
    d = mkfolder(tmp_path, "deepseek-m1", "deepseek-m1", [pack_row("LRV1-pilots-mess"), pack_row("LCC1-quiet-hours"),
                                                          pack_row("RV1-three-at-the-table")])
    cfg = {"providers": {"studioforge": {"type": "studioforge", "base_url": "http://x"}}, "models": []}
    ns = argparse.Namespace(folders=[str(d)], reader=["studioforge:Qwen/Qwen3.8-27B"], parts="chat/packs45",
                            adult=False, cap_usd=None, plan=True, out=None, judge_devices="2,3", judge_parallel=None,
                            judge_allow_devices="3", judge_instance_name=None, reader_thinking="on")
    assert cli.cmd_agree(ns, cfg) == 0
    assert "2 judged units" in capsys.readouterr().out
