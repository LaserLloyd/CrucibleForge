"""Root-cause fixes from the first autonomous run (2026-09-08):
1. cmd_judge snapshots residents BEFORE the judge lease evicts them.
2. _v2_start inherits requester/thread/task_run from a worker-written meta.json."""
import json
import types

import pytest

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
        def __init__(self, cfg, entries, include_judge=True, force_evict=False):
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

    def lease(cfg, force_evict=False):
        order.append("lease")
        raise Unavailable("no")
    monkeypatch.setattr("crucibleforge.judge.acquire_judge_lease", lease)
    args = types.SimpleNamespace(models="lbl", samples=None, smoke=False, force=False,
                                 force_evict=False, judge=None, judge_fallback=None)
    rc = cli.cmd_judge(args, {"judge": {"candidates": []}, "models": [], "providers": {}})
    assert rc == 4
    assert order == ["snapshot", "lease", "restore"]
