"""Parallel judging (2026-11-08): N copies of the SAME judge on disjoint cards.

Covers: label sharding (hash + balanced plan), one writer per transcript file
(label locks), work stealing (a vacated / finished shard's rows are taken over
with nothing lost or doubled), the shared rig lock + per-GPU locks, the judge
copy's identity (rows keep the canonical judge id, the copy goes to
judge_instance), its lease (exact cards, own holder, never force, GPU 3 only
on purpose, priority 3, vacate URL), the vacate receiver, the launch-parity
check and the instance config/window."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

from crucibleforge import config, judge, shard, instances, studioforge, stopflag

ROOT = Path(__file__).resolve().parent.parent
CANON = "llmfan46/gemma-4-31B-it-uncensored-heretic-GGUF/gemma-4-31B-it-uncensored-heretic-Q8_0"
ALIAS = CANON + "@b"


# ------------------------------------------------------------------ helpers
def _rows(label: str, n: int) -> list[dict]:
    return [{"bench_run_id": "r1", "case_id": f"C{i}", "repeat": 1, "turn": None,
             "rubric": "rp_single", "needs_judge": True, "prompt": "p",
             "response": f"{label} scene {i} " + "x" * (i * 37 % 400)} for i in range(n)]


def _write(results: Path, data: dict[str, int]) -> None:
    results.mkdir(parents=True, exist_ok=True)
    for label, n in data.items():
        (results / f"transcripts_{label}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in _rows(label, n)) + "\n", encoding="utf-8")


def fake_verdict(row: dict) -> dict:
    """Deterministic 'judge': the same row always gets the same verdict."""
    import hashlib
    h = int(hashlib.sha256(row["response"].encode()).hexdigest(), 16)
    return {"judge_failed": False, "judge_raw": "{}", "refused": False,
            "scores": {"prose": h % 11, "voice": (h // 11) % 11}}


_FAKE = textwrap.dedent('''
    import json, sys, time, threading
    sys.path.insert(0, {root!r})
    from pathlib import Path
    from crucibleforge import config, judge, profiles
    # archive_default in the child: these rows predate profiles; the release flip made bench45 the bare default
    profiles.DEFAULT_PROFILE = profiles.ARCHIVE_PROFILE
    import tests.test_parallel_judge_20261108 as T
    config.set_results_dir(Path({results!r}))

    class Prov:
        type, name = "openai", "fake"

    class JC:
        def __init__(self, inst):
            from crucibleforge.instances import from_cand
            self.instance = from_cand({{"model_id": "J", **(inst or {{}})}})
            self.model_id, self.serve_id = "J", self.instance.sid
            self.concurrency, self.thinking, self.provider = 4, False, Prov()
            self.label = "J"

    inst = json.loads({inst!r})
    judge._load_judge = lambda *a, **k: JC(k.get("instance"))
    judge.run_canary = lambda jc, **k: None
    judge.check_instance_parity = lambda jc: None      # proven separately below
    judge._private_case_ids = lambda cfg: set()
    judge.STEAL_POLL_S = 0.2
    delay = {delay!r}
    stop = threading.Event()
    first = threading.Lock()
    started = [False]
    # EXP-20261113-19: the stop timer and the peer barrier count from this process's FIRST judged
    # row, not from its launch: on a loaded box the import alone outlasted stop_after, so the
    # stopped copy judged 0 rows and the other copy took everything (order/load-dependent fail)
    ready, wait_for = {ready!r}, {wait_for!r}

    def jr(jc, row, samples=1):
        with first:
            if not started[0]:
                started[0] = True
                if ready:
                    Path(ready).write_text("1")
                if {stop_after!r} is not None:
                    threading.Timer({stop_after!r}, stop.set).start()
                if wait_for:
                    t_end = time.monotonic() + 60
                    while not Path(wait_for).exists() and time.monotonic() < t_end:
                        time.sleep(0.01)
        time.sleep(delay)
        return T.fake_verdict(row)
    judge.judge_row = jr
    labels = json.loads({labels!r})
    steal = json.loads({steal!r})
    cfg = {{"models": [{{"name": l, "model_id": l, "provider": "fake"}}
                       for l in labels + steal],
           "judge": {{"samples": 1}}, "providers": {{}}, "defaults": {{}}}}
    out = judge.run_judge(cfg, labels, instance=inst or None, steal=steal or None, stop=stop)
    print(json.dumps({{k: out.get(k) for k in ("judged", "skipped_labels", "stolen_labels",
                                              "stopped")}}))
''')


def _spawn(results: Path, labels, steal=(), inst=None, delay=0.01, stop_after=None,
           ready=None, wait_for=None):
    code = _FAKE.format(root=str(ROOT), results=str(results), labels=json.dumps(list(labels)),
                        steal=json.dumps(list(steal)), inst=json.dumps(inst or {}),
                        delay=delay, stop_after=stop_after,
                        ready=str(ready) if ready else None, wait_for=str(wait_for) if wait_for else None)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env, cwd=str(ROOT))


def _final(results: Path, label: str) -> dict:
    config.set_results_dir(results)
    return {(r["case_id"]): r for r in config.load_transcripts(label)}


# ------------------------------------------------------------------ sharding
def test_parse_shard():
    assert shard.parse_shard("1/2") == (1, 2) and shard.parse_shard(" 3 / 3 ") == (3, 3)
    for bad in ("0/2", "3/2", "x", "1/0", ""):
        with pytest.raises(ValueError):
            shard.parse_shard(bad)


def test_hash_shard_is_stable_disjoint_and_complete():
    labels = [f"model-{i}" for i in range(40)]
    parts = [shard.shard_labels(labels, (i, 3)) for i in (1, 2, 3)]
    assert sorted(sum(parts, [])) == sorted(labels)
    assert all(not set(a) & set(b) for a in parts for b in parts if a is not b)
    # a known value pins the hash (process/seed independent: sha256)
    assert shard.hash_shard("model-0", 2) == shard.hash_shard("model-0", 2)
    out = subprocess.run([sys.executable, "-c",
                          "from crucibleforge.shard import hash_shard;"
                          "print([hash_shard(f'model-{i}', 3) for i in range(40)])"],
                         capture_output=True, text=True, cwd=str(ROOT),
                         env={**os.environ, "PYTHONHASHSEED": "123"})
    assert out.stdout.strip() == str([shard.hash_shard(f"model-{i}", 3) for i in range(40)])


def test_balanced_split_is_deterministic_and_even():
    w = {"a": 10, "b": 9, "c": 8, "d": 3, "e": 2, "f": 2, "g": 1}
    one = shard.balanced(w, 2)
    assert one == shard.balanced(dict(reversed(list(w.items()))), 2)
    loads = [sum(w[l] for l in b) for b in one]
    assert abs(loads[0] - loads[1]) <= 1
    assert sorted(sum(one, [])) == sorted(w)


def test_plan_ledger_and_verify(tmp_path):
    _write(tmp_path, {"m1": 5, "m2": 3})
    config.set_results_dir(tmp_path)
    pend = {l: judge._pending_rows(l) for l in ("m1", "m2")}
    plan = shard.make_plan(["m1", "m2"], 2, pend, lambda r: 1, config.transcript_path)
    assert plan["ledger"]["m1"] and plan["file_bytes"]["m1"] > 0
    assert sorted(sum((s["labels"] for s in plan["shards"]), [])) == ["m1", "m2"]
    v = shard.verify(plan, config.transcript_path)
    assert v["judged"] == 0 and v["unjudged"] == 8 and v["ok"]
    # judge one row twice -> a duplicate
    r = dict(pend["m1"][0], judge=fake_verdict(pend["m1"][0]))
    config.append_transcript("m1", r)
    config.append_transcript("m1", r)
    v = shard.verify(plan, config.transcript_path)
    assert v["duplicates"] == 1 and not v["ok"]


# ------------------------------------------------------------------ locks
def test_label_lock_excludes_a_second_holder_even_in_process(tmp_path):
    a, b = shard.LabelLocks(tmp_path), shard.LabelLocks(tmp_path)
    assert a.acquire(["x", "y"]) == (["x", "y"], [])
    assert b.acquire(["y", "z"]) == (["z"], ["y"])
    a.release()
    assert b.acquire(["y"]) == (["y"], [])
    b.release()


def test_run_judge_skips_a_label_another_process_holds(tmp_path, monkeypatch):
    _write(tmp_path, {"m1": 3, "m2": 2})
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    other = shard.LabelLocks(tmp_path)
    other.acquire(["m2"])
    try:
        p = _spawn(tmp_path, ["m1", "m2"])
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
        res = json.loads(out.strip().splitlines()[-1])
        assert res["judged"] == 3 and res["skipped_labels"] == ["m2"]
    finally:
        other.release()
    assert all("judge" not in r for r in _final(tmp_path, "m2").values())


def test_shared_rig_lock_admits_shards_and_excludes_everything_else(tmp_path, monkeypatch):
    from crucibleforge import cli
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    f = cli.acquire_rig_lock(shared=True)
    probe = textwrap.dedent(f'''
        import fcntl
        f = open({str(tmp_path / ".rig.lock")!r}, "a+")
        out = []
        for mode in (fcntl.LOCK_SH, fcntl.LOCK_EX):
            try:
                fcntl.flock(f.fileno(), mode | fcntl.LOCK_NB); out.append("ok")
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except BlockingIOError:
                out.append("blocked")
        print(",".join(out))
    ''')
    try:
        r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
        assert r.stdout.strip() == "ok,blocked"     # another shard yes, bench-all no
    finally:
        f.close()


def test_device_locks_keep_two_shards_off_one_card(tmp_path):
    a = shard.DeviceLocks(tmp_path, [2, 3])
    a.acquire()
    try:
        with pytest.raises(shard.DeviceBusy):
            shard.DeviceLocks(tmp_path, [3]).acquire(wait_s=0)
        shard.DeviceLocks(tmp_path, [0, 1]).acquire(wait_s=0)   # disjoint: fine
    finally:
        a.release()


# --------------------------------------------- consistency: 1 judge vs 2 shards
def test_two_shards_equal_one_judge_rows_and_verdicts(tmp_path):
    data = {"m1": 9, "m2": 6, "m3": 7, "m4": 4, "m5": 5}
    single, sharded = tmp_path / "single", tmp_path / "sharded"
    _write(single, data)
    _write(sharded, data)
    p = _spawn(single, list(data))
    out, err = p.communicate(timeout=120)
    assert p.returncode == 0, err
    config.set_results_dir(sharded)
    pend = {l: judge._pending_rows(l) for l in data}
    plan = shard.make_plan(list(data), 2, pend, lambda r: 1, config.transcript_path)
    s1, s2 = (shard.plan_labels(plan, i) for i in (1, 2))
    pa = _spawn(sharded, s1, steal=s2, inst={"instance_name": "a"})
    pb = _spawn(sharded, s2, steal=s1,
                inst={"serve_model_id": "J@b", "instance_name": "b",
                      "instance_devices": [2, 3]})
    for pr in (pa, pb):
        out, err = pr.communicate(timeout=120)
        assert pr.returncode == 0, err
    v = shard.verify(plan, config.transcript_path)
    assert v["ok"] and v["judged"] == sum(data.values()) and v["unjudged"] == 0
    for label in data:
        one, two = _final(single, label), _final(sharded, label)
        assert one.keys() == two.keys()
        for cid in one:
            assert one[cid]["judge"]["scores"] == two[cid]["judge"]["scores"]
            assert two[cid]["judge_model"] == "J"                 # canonical identity
            ji = two[cid]["judge_instance"]
            assert ji["serve_id"] == ("J@b" if label in s2 else "J")
            assert "judge_instance" not in one[cid]               # plain run: no field


def test_a_stopped_shard_hands_its_rows_to_the_other(tmp_path):
    """The vacate path at the row level: shard B stops early (in-flight rows
    finish, queued stay pending), shard A waits for B's label locks, then
    takes every remaining row; nothing lost, nothing doubled."""
    data = {"b1": 30, "b2": 30, "a1": 3}
    _write(tmp_path, data)
    config.set_results_dir(tmp_path)
    pend = {l: judge._pending_rows(l) for l in data}
    plan = {"version": 1, "n": 2, "created": "",
            "shards": [{"index": 1, "labels": ["a1"]}, {"index": 2, "labels": ["b1", "b2"]}],
            "ledger": {l: [shard.row_key(r) for r in pend[l]] for l in data},
            "file_bytes": {l: config.transcript_path(l).stat().st_size for l in data}}
    pb = _spawn(tmp_path, ["b1", "b2"], steal=["a1"], delay=0.05, stop_after=0.6,
                inst={"serve_model_id": "J@b", "instance_name": "b"}, ready=tmp_path / ".b-ready")
    time.sleep(0.2)
    pa = _spawn(tmp_path, ["a1"], steal=["b1", "b2"], delay=0.01,
                inst={"instance_name": "a"}, wait_for=tmp_path / ".b-ready")
    ob, eb = pb.communicate(timeout=120)
    oa, ea = pa.communicate(timeout=120)
    assert pb.returncode == 0 and pa.returncode == 0, (eb, ea)
    rb, ra = json.loads(ob.strip().splitlines()[-1]), json.loads(oa.strip().splitlines()[-1])
    assert rb["stopped"] and 0 < rb["judged"] < 60
    assert sorted(ra["stolen_labels"]) == ["b1", "b2"]
    v = shard.verify(plan, config.transcript_path)
    assert v["ok"], v
    assert v["judged"] == 63 and v["unjudged"] == 0 and v["duplicates"] == 0


# ------------------------------------------------------------------ identity
class _SFProv:
    type, name, base_url, api_key = "studioforge", "sf", "http://rig.invalid/v1", ""
    lease_idle_ttl_s, wait_busy_s, day_single_card, busy_unload_after_s = 7200, 600, False, None

    def __init__(self):
        self._lease = None
        self._touched = False
        self.target_slots = 8
        self.min_slots = 4
        self.chats = []
        self.loads = []

    def mgmt_headers(self, client_name=None):
        return {"X-SF-Client": client_name or "crucibleforge"}

    def claim_devices(self, model_id, exclude=()):
        raise AssertionError("a judge copy must not choose its cards")

    def release_lease(self):
        self._lease = None

    def workers(self, mid):
        return 6

    def switch_model(self, mid, ctx):
        self.loads.append(mid)
        return 0.0

    def chat(self, mid, messages, client_name=None, **kw):
        self.chats.append((mid, client_name))
        return "ok"


def _cfg():
    return {"providers": {"sf": {"type": "studioforge", "base_url": "http://rig.invalid/v1"}},
            "models": [], "judge": {"candidates": []}}


def test_judge_client_sends_to_the_copy_and_keeps_the_canonical_id(monkeypatch):
    prov = _SFProv()
    monkeypatch.setattr(judge, "get_provider", lambda cfg, name: prov)
    cand = {"provider": "sf", "model_id": CANON, "serve_model_id": ALIAS,
            "instance_name": "b", "context_length": 16384}
    jc = judge.JudgeClient(_cfg(), cand)
    assert jc.model_id == CANON and jc.serve_id == ALIAS
    assert jc.holder == "crucibleforge-judge-b"
    jc.chat([{"role": "user", "content": "x"}])
    jc.load()
    assert prov.chats == [(ALIAS, "crucibleforge-judge-b")] and prov.loads == [ALIAS]


def test_copy_lease_names_exact_cards_never_force_and_refuses_gpu3_unless_allowed(monkeypatch):
    prov = _SFProv()
    monkeypatch.setattr(judge, "get_provider", lambda cfg, name: prov)
    monkeypatch.setattr(studioforge, "release_stale_leases", lambda *a, **k: [])
    got = {}

    def fake_acquire(base, key, hdrs, devices, **kw):
        got.update(devices=devices, hdrs=hdrs, **kw)
        return {"_lease_id": "L1"}

    monkeypatch.setattr(studioforge, "acquire_lease", fake_acquire)
    monkeypatch.setattr("atexit.register", lambda *a, **k: None)
    spec = {"provider": "sf", "model_id": CANON, "context_length": 16384}
    inst = {"serve_model_id": ALIAS, "instance_name": "b", "instance_devices": [2, 3]}
    with pytest.raises(judge.JudgeLeaseUnavailable, match="GPU \\[3\\]"):
        judge.acquire_judge_lease(_cfg(), force_evict=True, override=spec, instance=inst)
    prov._lease = None
    lease = judge.acquire_judge_lease(
        _cfg(), force_evict=True, override=spec,
        instance={**inst, "instance_allow_devices": [3]},
        vacate=("http://192.0.2.9:41000/vacate", "tok"))
    assert got["devices"] == [2, 3] and got["model_ids"] == [ALIAS]
    assert got["holder"] == "crucibleforge-judge-b" and got["force"] is False
    assert got["priority"] == 3 and got["vacate_url"].endswith("/vacate")
    assert got["vacate_token"] == "tok"
    assert lease["_model_id"] == ALIAS and lease["_devices"] == [2, 3]


def test_lease_body_carries_priority_and_vacate(monkeypatch):
    sent = {}

    def fake_mgmt(method, base, key, hdrs, path, json=None, timeout=None):
        sent.update(json)
        return 200, {"id": "L9"}

    monkeypatch.setattr(studioforge, "_mgmt", fake_mgmt)
    studioforge.acquire_lease("http://rig.invalid/v1", "", {}, [2, 3], model_ids=[ALIAS],
                              holder="crucibleforge-judge-b", priority=3,
                              vacate_url="http://h:1/vacate", vacate_token="t")
    assert sent["priority"] == 3 and sent["vacate_url"] == "http://h:1/vacate"
    assert sent["vacate_token"] == "t" and sent["force"] is False


def test_load_exact_steps_down_the_slot_ladder(monkeypatch):
    calls = []

    def fake_mgmt(method, base, key, hdrs, path, json=None, timeout=None):
        calls.append((path.rsplit("/", 1)[-1], (json or {}).get("parallel")))
        if path.endswith("/load") and json["parallel"] == 16:
            return 507, {"detail": "does not fit"}
        return 200, {}

    plans = iter([None, {"state": "ready", "parallel": 14, "ctx_size": 16384, "devices": [0, 1]}])
    monkeypatch.setattr(studioforge, "_mgmt", fake_mgmt)
    monkeypatch.setattr(studioforge, "loaded_plan", lambda *a, **k: next(plans))
    monkeypatch.setattr(studioforge, "wait_ready", lambda *a, **k: None)
    live = studioforge.load_exact(CANON, "http://rig.invalid/v1", "", {}, 16384, [12, 16, 14])
    assert live["parallel"] == 14
    assert [c for c in calls if c[0] == "load"] == [("load", 16), ("load", 14)]


# ------------------------------------------------------------------ parity
def _loaded(mid, devices, parallel, port, **over):
    argv = ["llama-server.exe", "--model", "gemma-4-31B-it-uncensored-heretic-Q8_0.gguf",
            "--alias", mid, "--host", "127.0.0.1", "--port", str(port),
            "--ctx-size", str(16384 * parallel), "--parallel", str(parallel),
            "--device", ",".join(f"CUDA{d}" for d in devices), "--tensor-split", "0.5,0.5",
            "--split-mode", "layer", "--main-gpu", str(devices[0]),
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--flash-attn", "on",
            "--no-webui", "--ubatch-size", "1024", "--cache-ram", "32603",
            "--reasoning-format", "none", "--batch-size", "4096", "--n-gpu-layers", "999"]
    e = {"model_id": mid, "state": "ready", "port": port, "resolved_engine_tag": "b11718",
         "launch_args": argv,
         "plan": {"ctx_size": 16384, "parallel": parallel, "kv_cache_type": "q8_0",
                  "kv_cache_type_v": "q8_0", "flash_attn": "on", "split_mode": "layer",
                  "devices": devices},
         "effective": {"ctx_per_slot": 16384, "kv_unified": False, "batch_size": 4096,
                       "ubatch_size": 1024, "spec_type": "none"}}
    for k, v in over.items():
        if k == "argv":
            e["launch_args"] = v(e["launch_args"])
        else:
            e[k] = v
    return e


def test_parity_ignores_only_per_copy_flags():
    a = _loaded(CANON, [0, 1], 16, 18100)
    b = _loaded(ALIAS, [2, 3], 6, 18101)
    assert instances.parity_problems(a, b) == []


@pytest.mark.parametrize("change", [
    lambda argv: [x if x != "none" else "deepseek" for x in argv],        # reasoning format
    lambda argv: [x if x != "1024" else "512" for x in argv],             # ubatch
    lambda argv: [x if not x.endswith(".gguf") else "other-Q4_K_M.gguf" for x in argv],
    lambda argv: argv + ["--chat-template-file", "x.jinja"],
])
def test_parity_catches_a_launch_difference(change):
    a = _loaded(CANON, [0, 1], 16, 18100)
    b = _loaded(ALIAS, [2, 3], 6, 18101, argv=change)
    assert instances.parity_problems(a, b)


def test_parity_catches_ctx_per_slot_and_engine():
    a = _loaded(CANON, [0, 1], 16, 18100)
    b = _loaded(ALIAS, [2, 3], 6, 18101, resolved_engine_tag="b99999")
    b["plan"]["ctx_size"] = 8192
    probs = " ".join(instances.parity_problems(a, b))
    assert "engine" in probs and "ctx_size" in probs


def test_registry_parity_alias_rules():
    canon = {"id": CANON, "quantization": "Q8_0", "arch": "gemma4",
             "studioforge": {"size_bytes": 1, "is_virtual": False}}
    alias = {"id": ALIAS, "quantization": "Q8_0", "arch": "gemma4",
             "studioforge": {"size_bytes": 1, "is_virtual": True, "base_model_id": CANON,
                             "adapters": [], "preset": None}}
    assert instances.registry_problems(canon, alias) == []
    bad = json.loads(json.dumps(alias))
    bad["studioforge"]["preset"] = {"system_prompt": "be nice"}
    bad["studioforge"]["adapters"] = [{"adapter_id": "x"}]
    assert len(instances.registry_problems(canon, bad)) == 2
    assert instances.registry_problems(canon, None)


def test_check_instance_parity_waits_for_the_canonical_then_proves(monkeypatch):
    prov = _SFProv()
    monkeypatch.setattr(judge, "get_provider", lambda cfg, name: prov)
    jc = judge.JudgeClient(_cfg(), {"provider": "sf", "model_id": CANON,
                                    "serve_model_id": ALIAS, "instance_name": "b"})
    canon = {"id": CANON, "quantization": "Q8_0", "arch": "gemma4", "studioforge": {"size_bytes": 1}}
    alias = {"id": ALIAS, "quantization": "Q8_0", "arch": "gemma4",
             "studioforge": {"size_bytes": 1, "is_virtual": True, "base_model_id": CANON}}
    monkeypatch.setattr(studioforge, "list_models_full", lambda *a, **k: [canon, alias])
    states = iter([{"loaded": [_loaded(ALIAS, [2, 3], 6, 18101)]},
                   {"loaded": [_loaded(ALIAS, [2, 3], 6, 18101), _loaded(CANON, [0, 1], 16, 18100)]}])
    monkeypatch.setattr(studioforge, "status", lambda *a, **k: next(states))
    monkeypatch.setattr(stopflag, "sleep", lambda s: False)
    judge.check_instance_parity(jc, wait_s=60)
    # a mismatch refuses
    monkeypatch.setattr(studioforge, "status", lambda *a, **k: {"loaded": [
        _loaded(ALIAS, [2, 3], 6, 18101, resolved_engine_tag="b1"),
        _loaded(CANON, [0, 1], 16, 18100)]})
    with pytest.raises(judge.JudgeError, match="launch differs"):
        judge.check_instance_parity(jc, wait_s=0)
    # canonical never shows up: refused, not assumed
    monkeypatch.setattr(studioforge, "status",
                        lambda *a, **k: {"loaded": [_loaded(ALIAS, [2, 3], 6, 18101)]})
    with pytest.raises(judge.JudgeError, match="cannot be\\s+proven|cannot be proven"):
        judge.check_instance_parity(jc, wait_s=0)


# ------------------------------------------------------------------ vacate
def test_vacate_listener_token_and_lease_checks():
    hits = []
    v = instances_vac = None
    from crucibleforge.vacate import VacateListener
    v = VacateListener("127.0.0.1", 0, on_vacate=hits.append).start()
    v.lease_id = "L1"
    try:
        def post(token, lease="L1", path="/vacate"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{v.port}{path}", method="POST",
                data=json.dumps({"lease_id": lease, "deadline_s": 180}).encode(),
                headers={"Content-Type": "application/json", "X-SF-Lease-Id": lease,
                         **({"X-SF-Vacate-Token": token} if token else {})})
            try:
                return urllib.request.urlopen(req, timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code
        assert post(None) == 403 and post("wrong") == 403
        assert post(v.token, lease="L2") == 404 and post(v.token, path="/x") == 404
        assert not v.event.is_set()
        assert post(v.token) == 202 and v.event.wait(2)
        assert post(v.token) == 202                 # idempotent: fires once
        time.sleep(0.2)
        assert len(hits) == 1 and hits[0]["lease_id"] == "L1"
    finally:
        v.close()
    assert v.url.startswith("http://127.0.0.1:") and v.url.endswith("/vacate")


def test_vacate_instance_stops_drains_aborts_releases_and_unloads(monkeypatch):
    prov = _SFProv()
    prov._lease = {"_lease_id": "L1"}
    events = []
    prov.release_lease = lambda: events.append("release")
    monkeypatch.setattr(judge, "get_provider", lambda cfg, name: prov)
    monkeypatch.setattr(studioforge, "loaded_plan",
                        lambda *a, **k: {"active_requests": 0, "state": "ready"})
    monkeypatch.setattr(studioforge, "unload_models",
                        lambda b, k, h, mids: events.append(("unload", tuple(mids))))
    stop = threading.Event()
    spec = {"provider": "sf", "model_id": CANON}
    try:
        t0 = time.time()
        judge.vacate_instance(_cfg(), stop, spec, {"serve_model_id": ALIAS, "instance_name": "b"},
                              {"deadline_s": 180})
        assert time.time() - t0 < 5
        assert stop.is_set() and stopflag.ABORT.is_set()
        assert events == ["release", ("unload", (ALIAS,))]
    finally:
        stopflag.ABORT.clear()


# ------------------------------------------------------------------ config
def test_instances_config_window_and_disjoint_cards():
    cfg = {"instances": {CANON: [
        {"name": "a", "devices": [0, 1], "parallel": [16, 14, 12]},
        {"name": "b", "serve_id": ALIAS, "devices": [2, 3], "parallel": [6, 5],
         "window": "22:00-08:00", "vacate": True, "allow_devices": [3], "priority": 3}]}}
    a, b = instances.configured(cfg, CANON)
    assert a.parallel == (16, 14, 12) and not a.is_copy and a.sid == CANON
    assert b.is_copy and b.sid == ALIAS and b.vacate and b.priority == 3
    assert instances.from_cand({"model_id": CANON, **instances.to_cand(b)}) == b
    bad = {"instances": {CANON: [{"name": "a", "devices": [0, 1]},
                                 {"name": "b", "devices": [1, 2]}]}}
    with pytest.raises(ValueError, match="GPU 1"):
        instances.configured(bad, CANON)
    assert instances.window_state("22:00-08:00", datetime(2026, 11, 8, 23, 0))[0]
    assert instances.window_state("22:00-08:00", datetime(2026, 11, 9, 7, 59))[0]
    assert not instances.window_state("22:00-08:00", datetime(2026, 11, 9, 12, 0))[0]
    with pytest.raises(ValueError):
        instances.parse_window("25:00-01:00")


def test_shard_restore_unloads_only_its_own_model(monkeypatch):
    from crucibleforge.providers import Provider
    p = Provider(name="sf", type="studioforge", base_url="http://rig.invalid/v1")
    p.own_unload_only, p._touched = True, True
    p._plans = {ALIAS: {}, CANON: {}}
    seen = []
    monkeypatch.setattr(studioforge, "unload_models",
                        lambda b, k, h, mids: seen.append(sorted(mids)))
    monkeypatch.setattr(studioforge, "unload_all",
                        lambda *a, **k: pytest.fail("a shard must never sweep the rig"))
    p.restore([{"model_id": CANON}])
    assert seen == [[ALIAS]]


def test_pool_mode_two_processes_share_models_without_loss_or_doubles(tmp_path):
    """--pool: both processes get every model and take them one at a time;
    the slow one (B) is stopped mid-way (vacate) — A takes the rest."""
    data = {f"m{i}": 4 + i % 5 for i in range(12)}
    _write(tmp_path, data)
    config.set_results_dir(tmp_path)
    pend = {l: judge._pending_rows(l) for l in data}
    plan = shard.make_plan(list(data), 1, pend, lambda r: 1, config.transcript_path)
    allm = list(data)
    pa = _spawn(tmp_path, [], steal=allm, delay=0.02, inst={"instance_name": "a"},
                wait_for=tmp_path / ".b-ready")
    pb = _spawn(tmp_path, [], steal=allm, delay=0.08, stop_after=0.8,
                inst={"serve_model_id": "J@b", "instance_name": "b"}, ready=tmp_path / ".b-ready")
    oa, ea = pa.communicate(timeout=120)
    ob, eb = pb.communicate(timeout=120)
    assert pa.returncode == 0 and pb.returncode == 0, (ea, eb)
    ra, rb = (json.loads(o.strip().splitlines()[-1]) for o in (oa, ob))
    assert ra["judged"] > 0 and rb["judged"] > 0            # both copies did real work
    assert ra["judged"] + rb["judged"] == sum(data.values())   # every row exactly once
    v = shard.verify(plan, config.transcript_path)
    assert v["ok"] and v["unjudged"] == 0 and v["judged"] == sum(data.values()), v


def test_run_judge_refuses_a_copy_whose_parity_is_unproven(tmp_path, monkeypatch):
    _write(tmp_path, {"m1": 2})
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)

    class Prov:
        type, name = "openai", "fake"

    class JC:
        model_id, serve_id, concurrency, thinking, label = "J", "J@b", 1, False, "J"
        provider = Prov()
        instance = instances.from_args("J", serve_id="J@b", name="b")

    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: JC())
    monkeypatch.setattr(judge, "run_canary", lambda jc, **k: None)
    monkeypatch.setattr(judge, "_private_case_ids", lambda cfg: set())
    cfg = {"models": [{"name": "m1", "model_id": "m1", "provider": "fake"}],
           "judge": {"samples": 1}, "providers": {}, "defaults": {}}
    with pytest.raises(judge.JudgeError, match="parity"):
        judge.run_judge(cfg, ["m1"], instance={"serve_model_id": "J@b", "instance_name": "b"})
    assert all("judge" not in r for r in config.load_transcripts("m1"))
