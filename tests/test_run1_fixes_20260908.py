"""Root-cause fixes from the first autonomous run (2026-09-08):
1. cmd_judge snapshots residents BEFORE the judge lease evicts them.
2. _v2_start inherits requester/thread/task_run from a worker-written meta.json."""
import json
import types


from crucibleforge import cli


def test_v2_start_inherits_routing_from_preexisting_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", tmp_path)
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text(json.dumps({
        "schema": 1, "id": "r1", "producer": "ds_flash", "kind": "spawn",
        "requester_session": "agent:main:thread-x", "thread_id": None, "task_run_id": "t9",
        "child_session": "agent:ds_flash:abc", "status": "running"}))
    args = types.SimpleNamespace(run_id="r1", cmd="all", models="m", requester=None,
                                 deliver_to=None, task_run_id=None)
    for k in ("CRUCIBLEFORGE_REQUESTER", "CRUCIBLEFORGE_DELIVER_TO", "CRUCIBLEFORGE_TASK_RUN_ID"):
        monkeypatch.delenv(k, raising=False)
    state = cli._v2_start(args)
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["requester_session"] == "agent:main:thread-x"
    assert meta["task_run_id"] == "t9"
    assert meta["child_session"] == "agent:ds_flash:abc"      # extra field rides along
    assert meta["producer"] == cli.V2_PRODUCER                 # ours, not the worker's
    assert meta["status"] == "running" and meta["delivered"] is False
    # explicit flags still win over the inherited values
    args2 = types.SimpleNamespace(run_id="r1", cmd="all", models="m", requester="agent:main:other",
                                  deliver_to="daily-main", task_run_id=None)
    cli._v2_start(args2)
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["requester_session"] == "agent:main:other" and meta["thread_id"] == "daily-main"
    # finish keeps everything
    monkeypatch.setattr(cli, "_v2_report_body", lambda *a, **k: "# r\n")
    cli._v2_finish("all", args2, {}, 0, state | {"requester_session": "agent:main:other",
                                                    "thread_id": "daily-main"})
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["status"] == "done" and meta["child_session"] == "agent:ds_flash:abc"


def test_cmd_judge_snapshots_before_lease(monkeypatch):
    order = []

    class Guard:
        def __init__(self, cfg, entries, include_judge=True, force_evict=False, judge=None):
            order.append("snapshot")
        def restore(self):
            order.append("restore")
        def busy(self):
            return []
        def serving(self):
            return []

    class Unavailable(Exception):
        holder = None

    monkeypatch.setattr(cli, "_ProviderGuard", Guard)
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda args, cfg: (cfg, None, None))
    monkeypatch.setattr(cli, "resolve_models", lambda cfg, m: [{"name": "lbl"}])
    monkeypatch.setattr("crucibleforge.judge.pending_judge_rows", lambda *a, **k: 1)
    monkeypatch.setattr("crucibleforge.judge.JudgeLeaseUnavailable", Unavailable)

    def lease(cfg, force_evict=False, override=None):
        order.append("lease")
        raise Unavailable("no")
    monkeypatch.setattr("crucibleforge.judge.acquire_judge_lease", lease)
    args = types.SimpleNamespace(models="lbl", samples=None, smoke=False, force=False,
                                 force_evict=False, judge=None, judge_fallback=None)
    rc = cli.cmd_judge(args, {"judge": {"candidates": []}, "models": [], "providers": {}})
    assert rc == 4
    assert order == ["snapshot", "lease", "restore"]


def test_detach_builds_a_transient_unit_without_the_flag_and_without_the_pin(monkeypatch):
    """--detach re-launches the same command under systemd-run --user, drops
    the flag itself, pins the run id, and never puts the PIN value in argv
    (the unit sources the env file)."""
    monkeypatch.setattr(cli, "ENV_FILE", cli.Path("/nonexistent/env"))
    args = types.SimpleNamespace(cmd="all", models="lbl", run_id="r-9")
    argv = ["all", "--models", "lbl", "--profile", "coding", "--detach", "--run-id", "r-9", "--yes"]
    unit, cmd, run_id = cli._detach_argv(args, argv)
    assert unit == "crucibleforge-r-9" and run_id == "r-9"
    assert cmd[:4] == ["systemd-run", "--user", "--collect", "--quiet"]
    assert f"--unit={unit}" in cmd and "--setenv=CRUCIBLEFORGE_RUN_ID=r-9" in cmd
    script = cmd[-1]
    assert "--detach" not in script
    assert "uv run crucibleforge all --models lbl --profile coding --run-id r-9 --yes" in script
    assert "/nonexistent/env" in script          # sourced inside the unit, not passed as a value
    # a run id is minted and appended when the caller gave none
    monkeypatch.setattr(cli, "_resolve_run_id", lambda a: "minted-1")
    args2 = types.SimpleNamespace(cmd="run", models="lbl", run_id=None)
    unit2, cmd2, rid2 = cli._detach_argv(args2, ["run", "--models", "lbl", "--detach"])
    assert rid2 == "minted-1" and "--run-id minted-1" in cmd2[-1]
