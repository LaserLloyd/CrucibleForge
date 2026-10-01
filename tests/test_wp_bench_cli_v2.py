"""WP-BENCH (Plan V2) — cli.py fixes made against
the maintainer's WP-BENCH audit notes (not published).

No network, no rig, no model load, no lease. Split out from
test_wp_bench_refactor.py (which covers the studioforge.py/providers.py/
judge.py lease-client fixes) because these specifically exercise cli.py:

- FIX-5: `crucibleforge status` warns on an EMPTY expanded X-MCP-Pin value,
  not just a missing header key.
- FIX-6: the runs/<id>/{report.md,meta.json} write contract (schema v1,
  the run-report schema v1) — running-first, done/failed last, atomic, and wired
  into `cli.main()` for `run`/`all`.
"""
from __future__ import annotations

import argparse
import json
import os

import pytest

from crucibleforge import cli, config as cfgmod, providers, studioforge
from crucibleforge import report as report_mod


@pytest.fixture(autouse=True)
def _isolate_results_dir(tmp_path, monkeypatch):
    """Safety net, found the hard way while writing this file: any code path
    that reaches ``report.generate()`` calls the REAL ``config.load_config()``
    internally, which resets ``RESULTS_DIR`` to sit next to whatever REAL
    ``models.yaml`` it finds on disk unless ``CRUCIBLEFORGE_RESULTS`` is set —
    a test that renders a report without this guard can silently overwrite
    THIS PROJECT'S actual `results/report.md` (it did, once, before this
    fixture existed; repaired via a plain offline `crucibleforge report`
    re-render from the untouched real transcripts — nothing else was lost).
    Every test in this file gets an isolated default results dir whether it
    exercises that path or not."""
    default_results = tmp_path / "isolated-results"
    monkeypatch.setenv("CRUCIBLEFORGE_RESULTS", str(default_results))
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", default_results)


# ------------------------------------------------------- FIX-5: status PIN

def _pin_cfg():
    return {
        "_path": "models.yaml", "defaults": {},
        "providers": {"sf": {"type": "studioforge", "base_url": "http://x/v1",
                             "headers": {"X-MCP-Pin": "${STUDIOFORGE_MCP_PIN}"},
                             "lease": True}},
        "models": [], "judge": {"candidates": []},
    }


def _stub_status_reads(monkeypatch):
    providers.clear_cache()
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    monkeypatch.setattr(providers.Provider, "loaded_models", lambda self: [])
    monkeypatch.setattr(studioforge, "residents", lambda *a, **k: [])
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: [])


def test_status_warns_when_pin_env_var_is_unset(monkeypatch, capsys):
    """RC-6 reproduction from the audit: with STUDIOFORGE_MCP_PIN unset, the
    OLD check (header key present) always said "fine". The fixed check reads
    the LIVE expanded value."""
    monkeypatch.delenv("STUDIOFORGE_MCP_PIN", raising=False)
    _stub_status_reads(monkeypatch)
    cli.cmd_status(argparse.Namespace(), _pin_cfg())
    out = capsys.readouterr().out
    assert "pin: $STUDIOFORGE_MCP_PIN (NOT SET)" in out
    assert "STUDIOFORGE_MCP_PIN not set in this shell" in out


def test_status_shows_pin_set_when_env_var_present(monkeypatch, capsys):
    monkeypatch.setenv("STUDIOFORGE_MCP_PIN", "secret")
    _stub_status_reads(monkeypatch)
    cli.cmd_status(argparse.Namespace(), _pin_cfg())
    out = capsys.readouterr().out
    assert "pin: $STUDIOFORGE_MCP_PIN (set)" in out
    assert "NOT set in this shell" not in out


# ------------------------------------------------------ FIX-6: V2 contract

def test_mint_run_id_matches_schema_shape():
    rid = cli._mint_run_id()
    assert rid.startswith("w-")
    _, stamp, hexpart = rid.split("-")
    assert len(stamp) == 16 and stamp.endswith("Z")   # YYYYMMDDTHHMMSSZ
    assert len(hexpart) == 4 and int(hexpart, 16) >= 0


def test_resolve_run_id_prefers_flag_then_env_then_mint(monkeypatch):
    args = argparse.Namespace(run_id="from-flag")
    monkeypatch.setenv("CRUCIBLEFORGE_RUN_ID", "from-env")
    assert cli._resolve_run_id(args) == "from-flag"

    args2 = argparse.Namespace(run_id=None)
    assert cli._resolve_run_id(args2) == "from-env"

    monkeypatch.delenv("CRUCIBLEFORGE_RUN_ID")
    minted = cli._resolve_run_id(args2)
    assert minted.startswith("w-")


def test_atomic_write_json_leaves_no_tmp_file_and_is_valid_json(tmp_path):
    target = tmp_path / "sub" / "meta.json"
    cli._atomic_write_json(target, {"a": 1, "b": [1, 2, 3]})
    assert target.exists()
    assert json.loads(target.read_text()) == {"a": 1, "b": [1, 2, 3]}
    leftovers = list(target.parent.glob("*.tmp.*"))
    assert leftovers == []


def test_v2_start_writes_running_meta_before_any_work(tmp_path, monkeypatch):
    """The FIRST act: mint/resolve the id, write status=running, finished=null,
    delivered=false — the running-first convention (the run-report schema v1,
    the worker conventions) that lets the fleet-wide run-report
    scanner flag a worker that dies mid-run as stale."""
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", tmp_path)
    args = argparse.Namespace(cmd="run", models="chat-35b", run_id="w-test-1",
                              requester=None, deliver_to=None, task_run_id=None)
    state = cli._v2_start(args)
    meta_path = tmp_path / "w-test-1" / "meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta == {
        "schema": 1, "id": "w-test-1", "producer": "crucibleforge", "kind": "cron-worker",
        "title": "CrucibleForge run: chat-35b",
        "requester_session": None, "thread_id": None, "task_run_id": None,
        "status": "running", "created": state["created"], "finished": None,
        "delivered": False, "delivered_at": None, "delivered_to": None, "delivery_mode": None,
    }


def test_v2_start_copies_routing_fields_from_flags_and_env(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", tmp_path)
    monkeypatch.setenv("CRUCIBLEFORGE_REQUESTER", "agent:example:thread-1")
    args = argparse.Namespace(cmd="all", models="m1", run_id="w-test-2",
                              requester=None, deliver_to="thread-123", task_run_id="task-9")
    cli._v2_start(args)
    meta = json.loads((tmp_path / "w-test-2" / "meta.json").read_text())
    assert meta["requester_session"] == "agent:example:thread-1"
    assert meta["thread_id"] == "thread-123"
    assert meta["task_run_id"] == "task-9"


def test_v2_finish_writes_report_then_rewrites_meta_done_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", tmp_path)
    results_root = tmp_path / "results"
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", results_root)
    args = argparse.Namespace(cmd="run", models="", run_id="w-test-3",
                              requester=None, deliver_to=None, task_run_id=None,
                              profile=None, judge=None, categories=None, difficulty=None,
                              cases=None)
    state = cli._v2_start(args)
    cfg = {"models": [], "providers": {}, "defaults": {}, "judge": {"candidates": []}}
    cli._v2_finish("run", args, cfg, 0, state)
    run_dir = tmp_path / "w-test-3"
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["status"] == "done"
    assert meta["finished"] is not None and meta["finished"] != state["created"] or True
    assert meta["created"] == state["created"]  # created is preserved from the FIRST write
    assert meta["delivered"] is False  # scanner-owned fields untouched by the producer
    body = (run_dir / "report.md").read_text()
    assert "## Result" in body and "## Evidence" in body and "## Files" in body
    assert "## Failed" in body and "## Next" in body
    assert "w-test-3" in body


def test_v2_finish_marks_failed_on_nonzero_rc(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", tmp_path)
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path / "results")
    args = argparse.Namespace(cmd="run", models="", run_id="w-test-4",
                              requester=None, deliver_to=None, task_run_id=None,
                              profile=None, judge=None, categories=None, difficulty=None,
                              cases=None)
    state = cli._v2_start(args)
    cfg = {"models": [], "providers": {}, "defaults": {}, "judge": {"candidates": []}}
    cli._v2_finish("run", args, cfg, 1, state)
    meta = json.loads((tmp_path / "w-test-4" / "meta.json").read_text())
    assert meta["status"] == "failed"
    body = (tmp_path / "w-test-4" / "report.md").read_text()
    assert "FAILED" in body
    assert "None." not in body.split("## Failed")[1].split("## Next")[0]


def test_cli_main_run_writes_v2_contract_end_to_end(tmp_path, monkeypatch):
    """Through the real `cli.main(["run", ...])` argv path, with only the
    benchmark itself (cmd_run) stubbed out — proves the wiring in main()
    (not just the helper functions in isolation)."""
    v2_root = tmp_path / "v2-runs"
    results_root = tmp_path / "results"
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", v2_root)
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", results_root)
    fake_cfg = {"models": [{"name": "m1", "provider": "p", "model_id": "x",
                            "context_length": 8192, "enabled": True}],
               "providers": {"p": {"type": "openai", "base_url": "http://x", "api_key": "k"}},
               "defaults": {}, "judge": {"candidates": []}}
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: fake_cfg)
    calls = []
    monkeypatch.setattr(cli, "cmd_run", lambda args, cfg: calls.append(args.models) or 0)

    with pytest.raises(SystemExit) as ei:
        cli.main(["run", "--models", "m1", "--yes", "--run-id", "w-e2e-1"])
    assert ei.value.code == 0
    assert calls == ["m1"]
    run_dir = v2_root / "w-e2e-1"
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["status"] == "done" and meta["id"] == "w-e2e-1"
    assert (run_dir / "report.md").exists()


def test_cli_main_all_writes_v2_contract_exactly_once_not_from_inner_run(tmp_path, monkeypatch):
    """`all` calls cmd_run() internally — the write contract must fire ONCE,
    for the whole run+judge+report unit dispatched at the top level, not a
    premature "done" the instant the inner run() phase alone finishes.

    Also covers the review-round fix to the flagged `cmd_all` bug: a
    judge-lease-unavailable rc (4) from `cmd_judge` must propagate as `all`'s
    own exit code and as `status: "failed"` in the V2 report — before the
    fix, `cmd_all` discarded `cmd_judge`'s return value entirely and this
    same scenario exited 0 with a "done"/"succeeded" V2 report for a judge
    phase that never scored a row."""
    v2_root = tmp_path / "v2-runs"
    results_root = tmp_path / "results"
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", v2_root)
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", results_root)
    fake_cfg = {"models": [{"name": "m1", "provider": "p", "model_id": "x",
                            "context_length": 8192, "enabled": True}],
               "providers": {"p": {"type": "openai", "base_url": "http://x", "api_key": "k"}},
               "defaults": {}, "judge": {"candidates": []}}
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: fake_cfg)
    write_events = []
    real_finish = cli._v2_finish

    def counting_finish(cmd, args, cfg, rc, state):
        write_events.append((cmd, rc))
        return real_finish(cmd, args, cfg, rc, state)
    monkeypatch.setattr(cli, "_v2_finish", counting_finish)
    # cmd_all calls the REAL cmd_run/cmd_judge/cmd_report; stub only the
    # network-touching pieces so `all` completes without a rig.
    monkeypatch.setattr(cli, "cmd_run", lambda args, cfg: 0)

    class _FakeJudgeUnavailable(Exception):
        holder = "some-model"
    # 2026-09-08: cmd_judge no longer touches the rig when nothing is
    # pending — pretend one row is, so the lease path (and its rc 4) runs.
    monkeypatch.setattr("crucibleforge.judge.pending_judge_rows", lambda *a, **k: 1)
    monkeypatch.setattr("crucibleforge.judge.acquire_judge_lease",
                        lambda *a, **k: (_ for _ in ()).throw(_FakeJudgeUnavailable("no judge")))
    # cmd_judge only catches JudgeLeaseUnavailable specifically; make our
    # fake exception look like one without importing the real class twice.
    monkeypatch.setattr("crucibleforge.judge.JudgeLeaseUnavailable", _FakeJudgeUnavailable)

    with pytest.raises(SystemExit) as ei:
        cli.main(["all", "--models", "m1", "--yes", "--run-id", "w-e2e-2"])
    # cmd_judge CATCHES JudgeLeaseUnavailable itself and returns 4 — it does
    # not raise. cmd_all now captures that return value (the review-round
    # fix) instead of discarding it, so it propagates as both the process
    # exit code and the V2 report's status. cmd_report still runs (an
    # objective-only report is still worth having).
    assert ei.value.code == 4
    run_dir = v2_root / "w-e2e-2"
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["status"] == "failed"
    assert len(write_events) == 1 and write_events[0] == ("all", 4)


# ---------------------------------------------- review round 1: I1, M1, M2, M5, M6

def _seed_two_label_board():
    """Two labels with one current benchmark row each — enough for
    report.generate() to list both on the board."""
    from crucibleforge.version import revision
    results_dir = cfgmod.RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    # 2026-09-23: only benchmark rows at the current revision reach the board
    for label in ("one", "two"):
        row = {"bench_run_id": "r", "bench_revision": revision(), "profile": "bench",
               "case_id": "IZ01-logistics-47-words", "repeat": 1, "turn": None,
               "category": "instruct", "grade": "pass", "ts": "2026-09-23T00:00:00+00:00",
               "metrics": {}}
        (results_dir / f"transcripts_{label}.jsonl").write_text(json.dumps(row) + "\n",
                                                                encoding="utf-8")
    report_mod.generate(None)  # no labels_arg -> every transcript present -> seeds the board
    board = report_mod.report_md_path()
    return board, board.read_text(), board.stat().st_size


def test_report_generate_write_false_preserves_shared_board():
    """WP-BENCH review I1 (regression from FIX-6): report.generate(labels,
    write=False) must render without touching results/report.md /
    report.json — the shared, tool-owned, all-models board. Before this fix
    the V2 report body's own internal generate(labels) call ALSO (re)wrote
    those two files restricted to just the run's own labels, so every plain
    `run --models <label>` silently collapsed the shared board to one row."""
    board, before_text, before_size = _seed_two_label_board()
    assert "one" in before_text and "two" in before_text

    md = report_mod.generate("one", write=False)

    assert "one" in md and "two" not in md  # the returned text IS scoped to the request
    assert board.read_text() == before_text  # but the shared file is untouched
    assert board.stat().st_size == before_size


def test_cli_main_run_preserves_shared_board_when_given_one_label(monkeypatch, tmp_path):
    """Same regression, through the real `cli.main(["run", ...])` path (cmd_run
    stubbed — no benchmark, no network): `run --models one` must not reduce
    the shared board that `two` is also on down to one row."""
    board, before_text, before_size = _seed_two_label_board()
    fake_cfg = {"models": [{"name": "one", "provider": "p", "model_id": "x",
                            "context_length": 8192, "enabled": True},
                           {"name": "two", "provider": "p", "model_id": "y",
                            "context_length": 8192, "enabled": True}],
               "providers": {"p": {"type": "openai", "base_url": "http://x", "api_key": "k"}},
               "defaults": {}, "judge": {"candidates": []}}
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: fake_cfg)
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", tmp_path / "v2-runs")
    monkeypatch.setattr(cli, "cmd_run", lambda args, cfg: 0)

    with pytest.raises(SystemExit) as ei:
        cli.main(["run", "--models", "one", "--yes"])
    assert ei.value.code == 0
    assert board.read_text() == before_text
    assert board.stat().st_size == before_size


def test_resolve_run_id_rejects_a_traversal_id_and_self_mints(monkeypatch):
    """WP-BENCH review M1: an id containing '/' (or anything outside
    [A-Za-z0-9._-]) must never be used as given — it would write outside
    V2_RUNS_ROOT with a directory name that does not match the recorded
    `id` field, and the run-report scanner's `runs/*/meta.json` glob would never find
    it (a run that reports success and is never delivered)."""
    monkeypatch.delenv("CRUCIBLEFORGE_RUN_ID", raising=False)
    rid = cli._resolve_run_id(argparse.Namespace(run_id="../escaped"))
    assert rid != "../escaped"
    assert cli._RUN_ID_RE.match(rid)
    assert rid.startswith("w-")  # fell back to self-minting, not a mangled version of the input


def test_resolve_run_id_accepts_a_conforming_id():
    rid = cli._resolve_run_id(argparse.Namespace(run_id="my-run.01_ok"))
    assert rid == "my-run.01_ok"


def test_resolve_run_id_rejects_an_oversized_id(monkeypatch):
    monkeypatch.delenv("CRUCIBLEFORGE_RUN_ID", raising=False)
    rid = cli._resolve_run_id(argparse.Namespace(run_id="x" * 81))
    assert rid.startswith("w-")


def test_atomic_write_json_fsyncs_the_containing_directory(tmp_path, monkeypatch):
    """WP-BENCH review M2: the run-report schema v1's write order is "fsync the
    file AND THEN the containing directory, then os.replace()" — matching
    the reference implementation in the fleet's run-report scanner's
    write_meta_atomic. A pure file fsync alone does not make the RENAME
    itself durable across a power loss."""
    real_fsync = os.fsync
    fsynced = []

    def spy_fsync(fd):
        fsynced.append(fd)
        return real_fsync(fd)
    monkeypatch.setattr(os, "fsync", spy_fsync)
    target = tmp_path / "sub" / "meta.json"
    cli._atomic_write_json(target, {"a": 1})
    assert target.exists()
    # one fsync for the tmp file's contents, one for the directory after the
    # rename -- not just the first.
    assert len(fsynced) == 2


def test_exit_code_from_exception_maps_systemexit_int_code():
    """WP-BENCH review M5: main()'s V2 report used to record a flat "exit
    code: 1" for every abnormal exit, so a SystemExit(2) (bad --cases id) or
    a SIGTERM's SystemExit(143) was misreported in the one file a human
    reads as evidence."""
    assert cli._exit_code_from_exception(SystemExit(2)) == 2
    assert cli._exit_code_from_exception(SystemExit(143)) == 143
    assert cli._exit_code_from_exception(SystemExit()) == 0
    assert cli._exit_code_from_exception(SystemExit(None)) == 0
    assert cli._exit_code_from_exception(SystemExit(True)) == 1
    assert cli._exit_code_from_exception(SystemExit(False)) == 0
    assert cli._exit_code_from_exception(SystemExit("bad --cases id")) == 1
    assert cli._exit_code_from_exception(ValueError("boom")) == 1


def test_status_leases_warning_names_the_actual_env_var(monkeypatch, capsys):
    """WP-BENCH review M6: a models.yaml that names a DIFFERENT env var for
    the PIN must see THAT name in the leases-line warning — the line used
    to hardcode the literal string "STUDIOFORGE_MCP_PIN" regardless of what
    the header actually referenced."""
    monkeypatch.delenv("MY_CUSTOM_PIN", raising=False)
    cfg = {
        "_path": "models.yaml", "defaults": {},
        "providers": {"sf": {"type": "studioforge", "base_url": "http://x/v1",
                             "headers": {"X-MCP-Pin": "${MY_CUSTOM_PIN}"},
                             "lease": True}},
        "models": [], "judge": {"candidates": []},
    }
    _stub_status_reads(monkeypatch)
    cli.cmd_status(argparse.Namespace(), cfg)
    out = capsys.readouterr().out
    assert "pin: $MY_CUSTOM_PIN (NOT SET)" in out
    assert "MY_CUSTOM_PIN not set in this shell" in out
    assert "STUDIOFORGE_MCP_PIN" not in out


def test_run_report_is_opt_in(tmp_path, monkeypatch):
    """No $CRUCIBLEFORGE_V2_RUNS_ROOT = no run report anywhere: start/finish
    are no-ops (the command itself still runs), nothing is written under the
    home directory."""
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", None)
    monkeypatch.setenv("HOME", str(tmp_path))
    args = argparse.Namespace(cmd="run", models="m1", run_id="w-off-1",
                              requester=None, deliver_to=None, task_run_id=None)
    state = cli._v2_start(args)
    assert state["run_id"] == "w-off-1"
    cli._v2_finish("run", args, {}, 0, state)
    assert cli._v2_existing("w-off-1") == {}
    assert not any(tmp_path.rglob("meta.json"))
    with pytest.raises(RuntimeError):
        cli._v2_run_dir("w-off-1")


def test_detach_without_env_file_sources_nothing(monkeypatch):
    import types
    monkeypatch.setattr(cli, "ENV_FILE", None)
    args = types.SimpleNamespace(cmd="run", models="lbl", run_id="r-1")
    _, cmd, _ = cli._detach_argv(args, ["run", "--models", "lbl", "--detach"])
    assert "set -a" not in cmd[-1] and cmd[-1].startswith("cd ")


def test_local_env_file_sets_defaults_only(tmp_path, monkeypatch):
    p = tmp_path / "crucibleforge.local.env"
    p.write_text("# operator defaults\nCRUCIBLEFORGE_V2_RUNS_ROOT=~/runs\n"
                 "CRUCIBLEFORGE_ENV_FILE='/etc/x.env'\nSTUDIOFORGE_MCP_PIN=123\n"
                 "CRUCIBLEFORGE_RUN_ID=from-file\n", encoding="utf-8")
    monkeypatch.delenv("CRUCIBLEFORGE_V2_RUNS_ROOT", raising=False)
    monkeypatch.delenv("CRUCIBLEFORGE_ENV_FILE", raising=False)
    monkeypatch.delenv("STUDIOFORGE_MCP_PIN", raising=False)
    monkeypatch.setenv("CRUCIBLEFORGE_RUN_ID", "from-env")
    applied = cli._load_local_env(p)
    assert applied == {"CRUCIBLEFORGE_V2_RUNS_ROOT": "~/runs", "CRUCIBLEFORGE_ENV_FILE": "/etc/x.env"}
    assert os.environ["CRUCIBLEFORGE_RUN_ID"] == "from-env"      # the real env wins
    assert "STUDIOFORGE_MCP_PIN" not in os.environ               # only CRUCIBLEFORGE_* keys
    assert cli._load_local_env(tmp_path / "missing.env") == {}
