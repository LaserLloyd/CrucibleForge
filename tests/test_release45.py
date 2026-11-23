"""scripts/release45.py: the driver from finished run folders to a board (stubbed commands, synthetic folders)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release45 as R  # noqa: E402
import check_marks45 as CM  # noqa: E402

@pytest.fixture(autouse=True)
def _no_env_preflight(request, monkeypatch):
    if "preflight" not in request.node.name:
        monkeypatch.setattr(R.Release, "preflight_env", lambda self: None)


ROW = json.dumps({"case_id": "X-1", "repeat": 1, "response": "hello", "bench_run_id": "r"})


def mkfolder(tmp: Path, name: str, label: str) -> Path:
    f = tmp / name
    f.mkdir()
    (f / f"transcripts_{label}.jsonl").write_text(ROW + "\n", encoding="utf-8")
    (f / f"meta_{label}.json").write_text("{}", encoding="utf-8")
    return f


_VERDICT_KEY = {"check_classing_marks.py": "classing", "check_rplong_marks45.py": "chat_rplong",
                "check_pack_marks45.py": "chat_packs", "check_nsfw_pack_marks45.py": "nsfw_packs",
                "check_sanity45.py": "sanity"}


def fake_verdict(cmd) -> None:
    """What a real mark checker leaves behind (a fresh verdict entry); release45 refuses a step without one."""
    c = [str(x) for x in cmd]
    name = Path(c[1]).name if len(c) > 1 else ""
    if name in _VERDICT_KEY and "--results" in c:
        p = Path(c[c.index("--results") + 1]) / "marks45.json"
        cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        cur[_VERDICT_KEY[name]] = {"stub": True, "n": len(c)}
        if name == "check_classing_marks.py":         # the C6 block of the current rule (b-c6 refuses without it)
            from crucibleforge import classing45 as C
            cur["classing"]["C6_reader_refusal"] = {"status": "not_measured", "rule": C.C6_RULE}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cur), encoding="utf-8")
    elif name == "check_assistant_marks.py" and "--out" in c:
        Path(c[c.index("--out") + 1]).write_text(json.dumps({"ok": False, "reasons": [], "lines": []}), encoding="utf-8")


def fold_anchors(args: list[str]) -> list[str]:
    """Transitional: a check_marks45 without ``--anchors`` reads anchors as extra ``--folders``."""
    if "--anchors" not in args:
        return args
    i = args.index("--anchors")
    j = next((n for n in range(i + 1, len(args)) if args[n].startswith("--")), len(args))
    anchors, rest = args[i + 1:j], args[:i] + args[j:]
    k = rest.index("--folders") + 1
    while k < len(rest) and not rest[k].startswith("--"):
        k += 1
    return rest[:k] + anchors + rest[k:]


class Sim:
    """Stub for the commands the driver starts: records them and fakes their effect."""

    def __init__(self, monkeypatch, rewrite_rows=False, no_reader=False, chat_reader=True):
        self.cmds: list[list[str]] = []
        self.envs: list[dict] = []
        self.generated: set[str] = set()
        self.rewrite_rows, self.no_reader, self.chat_reader = rewrite_rows, no_reader, chat_reader
        monkeypatch.setattr(R.Release, "fix_needed", lambda s, f: ([] if f.name in self.generated else ["CB-F1"], []))
        monkeypatch.setattr(R.Release, "everyday_needed", lambda s, f: [])

    def runner(self, cmd, env, log):
        self.cmds.append([str(c) for c in cmd])
        self.envs.append(env)
        name = Path(cmd[1]).name if len(cmd) > 1 else ""
        if "run" in cmd and "--cases" in cmd:                         # a generating call
            res = Path(cmd[cmd.index("--results") + 1])
            self.generated.add(res.name)
            t = next(res.glob("transcripts_*.jsonl"))
            if self.rewrite_rows:
                t.write_text(ROW.replace("hello", "changed") + "\n", encoding="utf-8")
            else:
                with open(t, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"case_id": "CB-F1", "repeat": 1}) + "\n")
        if name == "read45.py" and self.no_reader:
            return 2
        if "report" in cmd and "--results" in cmd:                      # crucibleforge report: write a report.json
            res = Path(cmd[cmd.index("--results") + 1])
            label = next(res.glob("transcripts_*.jsonl")).name[len("transcripts_"):-len(".jsonl")]
            segs = {"code/writes": {"points": 3.0, "partial": 3.0, "max": 4.0, "margin": 0.1, "complete": True,
                                    "reason": None}}
            (res / "report.json").write_text(json.dumps({"models": {label: {"marks_input45": {
                "segments": segs, "ab_units": [], "know": None}}}}), encoding="utf-8")
        fake_verdict(cmd)
        if name == "check_marks45.py":                                  # the real mark check on the stub reports
            args = fold_anchors([str(c) for c in cmd[2:]])
            return CM.main(args)
        return 0

    def capturer(self, cmd, env):
        self.cmds.append([str(c) for c in cmd])
        if not self.chat_reader:
            return 1, "m: NO FITTING READER (none named); long scenes print pending"
        return 0, "m: reader r1 (qwen); run: crucibleforge agree --parts chat/j_rplong --reader r1 f\n"


def go(tmp, monkeypatch, argv, sim=None, **kw):
    sim = sim or Sim(monkeypatch, **kw)
    folders = sorted(p for p in tmp.iterdir() if p.is_dir() and not p.name.startswith("board"))
    rc = R.main(["--folders", *map(str, folders), "--board", str(tmp / "board45"), *argv], runner=sim.runner,
                capturer=sim.capturer)
    return rc, sim


#: the pre-2026-11-21 default order (item 13 moved row filling and the mark steps out of the default; these tests
#: still drive them, by name)
OLD = ["--only", "a-fix,a-everyday,c-assemble,c-longscene,c-packmarks,c-nsfwpackmarks,c-assistant,c-marks,c-sanity,"
                 "d-report,e-resolution"]


@pytest.fixture
def two(tmp_path):
    mkfolder(tmp_path, "r1", "model-a")
    mkfolder(tmp_path, "r2", "model-b")
    return tmp_path


def test_dry_run_prints_the_plan_and_writes_nothing(two, monkeypatch, capsys):
    rc, sim = go(two, monkeypatch, ["--dry-run", *OLD])
    out = capsys.readouterr().out
    assert rc == 0 and sim.cmds == []
    assert "release45 plan" in out and "[a-fix] r1" in out and "price:" in out and "dry run" in out
    assert not (two / "r1" / ".release45").exists() and not (two / "r1" / "release45.log").exists()
    assert not (two / "board45").exists()


def test_maintenance_marker_refuses_to_start(two, monkeypatch, tmp_path_factory):
    mk = tmp_path_factory.mktemp("m") / "maintenance"
    mk.write_text("pid=1", encoding="utf-8")
    monkeypatch.setenv("CF_MAINTENANCE_MARKER", str(mk))
    rc, sim = go(two, monkeypatch, [])
    assert rc == 5 and sim.cmds == []


def test_full_run_then_resume_does_nothing(two, monkeypatch):
    rc, sim = go(two, monkeypatch, OLD)
    assert rc == 0, sim.cmds
    first = len(sim.cmds)
    assert first > 8
    assert (two / "r1" / ".release45" / "a-fix.json").is_file()
    marker = json.loads((two / "r1" / ".release45" / "a-fix.json").read_text(encoding="utf-8"))
    assert marker["commit"] == R.tree_sha() and marker["ok"] is True
    # the combined board exists and carries the mark blocks the board reads
    body = json.loads((two / "board45" / "marks45.json").read_text(encoding="utf-8"))
    assert {"segments", "everyday", "act_beat"} <= set(body)
    assert (two / "r1" / "release45.log").read_text(encoding="utf-8").count("DONE a-fix") == 1
    rc, sim2 = go(two, monkeypatch, OLD, sim=Sim(monkeypatch))
    assert rc == 0 and sim2.cmds == []                              # every step current: nothing runs


def test_marker_goes_stale_when_the_tree_changes(two, monkeypatch):
    go(two, monkeypatch, [])
    monkeypatch.setattr(R, "tree_sha", lambda: "someotherco")
    rc, sim = go(two, monkeypatch, ["--only", "d"], sim=Sim(monkeypatch))
    assert rc == 0 and any("report" in c for c in sim.cmds)


def test_only_runs_the_named_steps(two, monkeypatch):
    rc, sim = go(two, monkeypatch, ["--only", "a-fix"])
    assert rc == 0
    assert all("run" in c for c in sim.cmds) and len(sim.cmds) == 2
    assert not (two / "board45" / "marks45.json").exists()


def test_a_changed_stored_row_stops_the_run(two, monkeypatch):
    rc, sim = go(two, monkeypatch, OLD, rewrite_rows=True)
    assert rc == 4
    assert "STORED ROW CHANGED" in (two / "r1" / "release45.log").read_text(encoding="utf-8")
    assert not (two / "r1" / ".release45" / "a-fix.json").exists()
    assert not any("read45" in " ".join(c) for c in sim.cmds)        # nothing after it ran


def test_rows_only_ever_get_appended(two, monkeypatch):
    t = next((two / "r1").glob("transcripts_*.jsonl"))
    first = t.read_text(encoding="utf-8").splitlines()[0]
    go(two, monkeypatch, OLD)
    lines = t.read_text(encoding="utf-8").splitlines()
    assert lines[0] == first and len(lines) == 2


def test_no_admitted_reader_stops_with_a_message(two, monkeypatch, capsys):
    # Part 1 (2026-11-21): b-classing left the default order; by name it still stops on a missing reader
    rc, sim = go(two, monkeypatch, ["--only", "b-classing"], no_reader=True)
    out = capsys.readouterr().out
    assert rc == 2 and "nothing was picked for you" in out
    assert not any(Path(c[1]).name == "check_marks45.py" for c in sim.cmds if len(c) > 1)


def test_no_chat_reader_stops(two, monkeypatch, capsys):
    rc, sim = go(two, monkeypatch, ["--only", "b-chat"], chat_reader=False)
    assert rc == 2 and "nothing was picked for you" in capsys.readouterr().out


def test_caps_pass_through(two, monkeypatch):
    monkeypatch.delenv("CF_SPEND_RUN_CAP_USD", raising=False)
    rc, sim = go(two, monkeypatch, ["--only", "a-fix", "--spend-cap-usd", "1.5", "--spend-run-cap-usd", "2",
                                    "--spend-total-cap-usd", "7", "--spend-ledger", "/x/ledger.jsonl"])
    assert rc == 0
    gen = [c for c in sim.cmds if "--cases" in c][0]
    assert gen[gen.index("--spend-cap-usd") + 1] == "1.5" and "--yes" in gen
    env = sim.envs[0]
    assert float(env["CF_SPEND_RUN_CAP_USD"]) == 2 and float(env["CF_SPEND_CAP_USD"]) == 7
    assert env["CF_SPEND_LEDGER"] == "/x/ledger.jsonl"


def test_a_folder_with_two_transcripts_is_refused(two, monkeypatch, capsys):
    (two / "r1" / "transcripts_other.jsonl").write_text(ROW + "\n", encoding="utf-8")
    rc, sim = go(two, monkeypatch, [])
    assert rc == 2 and "exactly one" in capsys.readouterr().out


def test_unknown_step_is_refused(two, monkeypatch):
    rc, sim = go(two, monkeypatch, ["--only", "zz"])
    assert rc == 2 and sim.cmds == []


def test_no_tty_needed(two, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(R.subprocess, "run", fake_run)
    assert R.default_runner(["true"], {}, None) == 0
    assert seen["stdin"] == subprocess.DEVNULL


def test_assemble_copies_and_never_touches_the_folders(two, monkeypatch):
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in two.rglob("transcripts_*.jsonl")}
    rc, sim = go(two, monkeypatch, ["--only", "c-assemble"])
    assert rc == 0
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}
    assert before == after
    assert sorted(p.name for p in (two / "board45").glob("transcripts_*.jsonl")) == \
        ["transcripts_model-a.jsonl", "transcripts_model-b.jsonl"]


def test_spread_keeps_other_blocks(two, monkeypatch):
    (two / "board45").mkdir()
    (two / "board45" / "marks45.json").write_text(json.dumps({"classing": {"x": 1}, "chat_rplong": {"y": 2}}), encoding="utf-8")
    (two / "r1" / "marks45.json").write_text(json.dumps({"segments": {"keep": 1}}), encoding="utf-8")
    sim = Sim(monkeypatch)
    rel = R.Release([two / "r1"], two / "board45", R.parse(["--folders", "x"]), sim.runner, sim.capturer)
    rel.spread(("classing", "chat_rplong"))
    body = json.loads((two / "r1" / "marks45.json").read_text(encoding="utf-8"))
    assert body == {"segments": {"keep": 1}, "classing": {"x": 1}, "chat_rplong": {"y": 2}}


def test_reader_step_with_a_stored_c1_file_passes_plain_strings(two, monkeypatch):
    """Pilot 2026-11-20: a board marks45.json made the driver put a Path into the command and crash in the log line."""
    (two / "board45").mkdir()
    (two / "board45" / "marks45.json").write_text("{}", encoding="utf-8")
    rc, sim = go(two, monkeypatch, ["--only", "b-classing", "--classing-cap-usd", "1.5"])
    assert rc == 0
    assert any("--c1" in c for c in sim.cmds)


def test_the_agree_line_read_chat45_prints_is_accepted_by_the_real_cli():
    """Pilot 2026-11-20: read_chat45 printed `--folders <f>` but `agree` takes the folders positionally."""
    src = (ROOT / "scripts" / "read_chat45.py").read_text(encoding="utf-8")
    assert "--folders {f}" not in src
    r = subprocess.run([sys.executable, "-m", "crucibleforge.cli", "agree", "--parts", "chat/j_rplong", "--reader", "x",
                        "--reader-thinking", "on", "--cap-usd", "1.0", "/nonexistent-folder-for-parse-test"],
                       capture_output=True, text=True, cwd=ROOT)
    assert "unrecognized arguments" not in (r.stderr or "") and "required" not in (r.stderr or "")


def test_seeding_the_admission_keeps_a_reader_measured_since(two, monkeypatch):
    """Pilot 2026-11-20: every driver start re-seeded the stored admission over the whole C1 block and dropped the
    results of readers measured after the seed file was written."""
    sim = Sim(monkeypatch)
    rel = R.Release([two / "r1"], two / "board45", R.parse(["--folders", "x"]), sim.runner, sim.capturer)
    (two / "board45").mkdir()
    (two / "board45" / "marks45.json").write_text(json.dumps({"classing": {"C1_constructed": {"status": "fail", "per_reader": {
        "R-Q": {"model": "q", "status": "fail"}, "ds": {"model": "old", "status": "fail"}}}, "other": {"keep": 1}}}), encoding="utf-8")
    rel.seed_admission({"C1_constructed": {"status": "pass", "per_reader": {"ds": {"model": "d", "status": "pass"}}}})
    got = json.loads((two / "board45" / "marks45.json").read_text(encoding="utf-8"))["classing"]
    assert got["C1_constructed"]["per_reader"]["R-Q"]["status"] == "fail"       # kept
    assert got["other"] == {"keep": 1}


def test_b_chat_is_not_done_while_a_scene_is_unread(two, monkeypatch, capsys):
    """Pilot 2026-11-20: b-chat marked itself done with 14 of 16 units unread (every hosted call 401)."""
    sim = Sim(monkeypatch)
    base = sim.capturer
    state = {"complete": False}

    def capturer(cmd, env):
        if "--verify" in [str(c) for c in cmd]:
            sim.cmds.append([str(c) for c in cmd])
            return (0, "m: 16 of 16 long scene(s) have both readings\n") if state["complete"] else \
                (1, "m: 14 of 16 long scene(s) have both readings; unread: a, b\n")
        return base(cmd, env)
    sim.capturer = capturer
    rc, _ = go(two, monkeypatch, ["--only", "b-chat", "--no-chat-hosted"], sim=sim)
    assert rc == 3 and "incomplete" in capsys.readouterr().out
    assert not list(two.glob("r1/.release45/b-chat.json"))        # not marked done: the next start resumes it
    state["complete"] = True
    rc, _ = go(two, monkeypatch, ["--only", "b-chat", "--no-chat-hosted"], sim=sim)
    assert rc == 0 and list(two.glob("r1/.release45/b-chat.json"))


def _cfg(tmp):
    p = tmp / "models.yaml"
    p.write_text("providers:\n  hostedp: {type: openai, base_url: 'https://x.invalid/v1', api_key_env: PF_TEST_KEY}\n"
                 "models:\n- {name: model-a, provider: hostedp, model_id: a, context_length: 8000, price: {input: 1, output: 1}}\n"
                 "- {name: deepseek-flash, provider: hostedp, model_id: d, context_length: 8000, price: {input: 1, output: 1}}\n",
                 encoding="utf-8")
    return p


def test_preflight_names_a_missing_config_in_one_line(two, monkeypatch, capsys):
    monkeypatch.delenv("CRUCIBLEFORGE_CONFIG", raising=False)
    rc, sim = go(two, monkeypatch, ["--only", "a-fix"])
    out = capsys.readouterr().out
    assert rc == 2 and "CRUCIBLEFORGE_CONFIG is not set" in out and sim.cmds == []


def test_preflight_names_a_missing_hosted_key(two, monkeypatch, capsys):
    monkeypatch.setenv("CRUCIBLEFORGE_CONFIG", str(_cfg(two.parent)))
    monkeypatch.delenv("PF_TEST_KEY", raising=False)
    rc, sim = go(two, monkeypatch, ["--only", "a-fix"])
    out = capsys.readouterr().out
    assert rc == 2 and "PF_TEST_KEY is not set" in out and "model-a" in out and sim.cmds == []
    monkeypatch.setenv("PF_TEST_KEY", "x")
    rc, sim = go(two, monkeypatch, ["--only", "a-fix"])
    assert rc == 0


def test_preflight_wants_the_chat_reader_key_unless_no_hosted(two, monkeypatch, capsys):
    monkeypatch.setenv("CRUCIBLEFORGE_CONFIG", str(_cfg(two.parent)))
    monkeypatch.delenv("PF_TEST_KEY", raising=False)
    rc, _ = go(two, monkeypatch, ["--only", "b-chat"])
    assert rc == 2 and "deepseek-flash" in capsys.readouterr().out
    rc, _ = go(two, monkeypatch, ["--only", "c-assemble"])
    assert rc == 0


def test_a_local_chat_reader_named_by_label_is_imported_from_the_file_agree_wrote(tmp_path):
    """Pilot 2 (DeepSeek folders): agree read 8 scenes with the local reader but `--import-agree` stored 0, because the
    records are filed under the model id the models-yaml label resolves to, not under the label."""
    import read_chat45 as RC
    from crucibleforge import rplong_mean as RM
    cfg = tmp_path / "models.yaml"
    cfg.write_text("models:\n- {name: r-label, model_id: org/Some-Reader-Q8, provider: p}\n", encoding="utf-8")
    rd = RM.ReaderSpec(id="r-label", family="qwen", hosted=False, provider="", role="R-Q", thinking=True)
    f = tmp_path / "folder"
    (f / "agree").mkdir(parents=True)
    assert RC.engine_model_id("r-label", str(cfg)) == "org/Some-Reader-Q8"
    assert RC.engine_model_id("nope", str(cfg)) is None
    assert RC.stored_reader_id(f, rd, None, str(cfg)) == "org/Some-Reader-Q8"     # no file under the label
    assert RC.stored_reader_id(f, rd, "given", str(cfg)) == "given"
    from crucibleforge import agree as AG
    AG.append_records(f, rd.key, [{"part": "chat/j_rplong", "unit": "u", "row_sha": "x"}])
    assert RC.stored_reader_id(f, rd, None, str(cfg)) is None                    # a file under the label wins


def test_a_scored_import_replaces_a_stored_unscored_record(tmp_path, monkeypatch, capsys):
    """Pilot 2: a scene the reader failed on was stored with no score; its later good reading was never imported."""
    import read_chat45 as RC
    from crucibleforge import agree as AG, rplong_mean as RM
    rd = RM.ReaderSpec(id="r", family="qwen", hosted=False, provider="", role="R-Q", thinking=True)
    monkeypatch.setattr(RC.AG, "read_folder", lambda f: ("lab", []))
    monkeypatch.setattr(RC.AG, "graded_family", lambda *a: "deepseek")
    monkeypatch.setattr(RC.RM, "load_readers", lambda c=None: {})
    monkeypatch.setattr(RC.RM, "pinned_reader", lambda *a, **k: rd)
    stamp = {"prompt_sha": "p", "model": "r"}
    feed = {"recs": [{"unit": "u", "reader": "r", "reader_stamp": stamp, "row_sha": "x", "score_reader": None}]}
    monkeypatch.setattr(RC.RM, "import_agree_records", lambda *a, **k: list(feed["recs"]))
    monkeypatch.setattr(RC, "stored_reader_id", lambda *a, **k: None)
    f = tmp_path / "f"
    f.mkdir()
    args = ["--results", str(tmp_path), "--folders", str(f), "--import-agree"]
    RC.main(args)
    feed["recs"] = [{**feed["recs"][0], "score_reader": 0.7}]
    RC.main(args)
    RC.main(args)                                                    # a third import adds nothing
    stored = RM.load_store(tmp_path, "lab")
    assert [v["score_reader"] for v in stored.values()] == [0.7]
    assert len(RM.store_path(tmp_path, "lab").read_text(encoding="utf-8").splitlines()) == 2
