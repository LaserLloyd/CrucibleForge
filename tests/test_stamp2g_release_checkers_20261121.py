"""Stamp-2 G6b / G7: release45 never logs a mark step DONE when its checker crashed or wrote no verdict; the
Assistant-basics step gets the reference/damaged identity from the addendum; anchors are per-model copies."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release45 as R  # noqa: E402
import check_assistant_marks as CA  # noqa: E402


@pytest.fixture(autouse=True)
def _no_env_preflight(monkeypatch):
    monkeypatch.setattr(R.Release, "preflight_env", lambda self: None)


ROW = json.dumps({"case_id": "X-1", "repeat": 1, "response": "hello", "bench_run_id": "r"})


def mkfolder(tmp: Path, name: str, label: str) -> Path:
    f = tmp / name
    f.mkdir(parents=True)
    (f / f"transcripts_{label}.jsonl").write_text(ROW + "\n", encoding="utf-8")
    return f


def _merge(path: Path, blocks: dict) -> None:
    cur = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    cur.update(blocks)
    path.write_text(json.dumps(cur), encoding="utf-8")


def _backdate(path: Path) -> None:
    old = time.time() - 3600
    os.utime(path, (old, old))


# step -> (extra argv, checker script, where the verdict goes: ("board", key) | ("assistant",) | ("folders",))
STEPS = {
    "b-c1": (["--classing-readers", "x"], "check_classing_marks.py", ("board", "classing")),
    "c-classing": ([], "check_classing_marks.py", ("board", "classing")),
    "c-longscene": ([], "check_rplong_marks45.py", ("board", "chat_rplong")),
    "c-packmarks": ([], "check_pack_marks45.py", ("board", "chat_packs")),
    "c-nsfwpackmarks": ([], "check_nsfw_pack_marks45.py", ("board", "nsfw_packs")),
    "c-assistant": ([], "check_assistant_marks.py", ("assistant",)),
    "c-marks": ([], "check_marks45.py", ("folders",)),
}


def run_step(tmp: Path, step: str, mode: str):
    """A stub checker: pass / fail / notmeasured write a fresh verdict; crash dies with a traceback and writes nothing;
    silent exits 0 and writes nothing. A stale block of the same key is pre-seeded (and backdated) in every case."""
    extra, script, where = STEPS[step]
    f1 = mkfolder(tmp, "r1", "model-a")
    board = tmp / "board45"
    board.mkdir()
    stale = {"stale": True}
    if where[0] == "board":
        _merge(board / "marks45.json", {where[1]: stale})
        _backdate(board / "marks45.json")
    elif where[0] == "assistant":
        (board / "assistant_marks.json").write_text(json.dumps(stale), encoding="utf-8")
        _backdate(board / "assistant_marks.json")
    else:
        _merge(f1 / "marks45.json", {k: stale for k in (*R.MARK_KEYS, "check_marks45")})
        _backdate(f1 / "marks45.json")
    rcs = {"pass": 0, "fail": 1, "notmeasured": 2, "crash": 1, "silent": 0}
    called = []

    def runner(cmd, env, log):
        name = Path(cmd[1]).name if len(cmd) > 1 else ""
        if name != script:
            return 0
        called.append(name)
        if mode in ("pass", "fail", "notmeasured"):
            blk = {"fresh": mode}
            if where[0] == "board":
                _merge(board / "marks45.json", {where[1]: blk})
            elif where[0] == "assistant":
                Path(cmd[cmd.index("--out") + 1]).write_text(json.dumps(blk), encoding="utf-8")
            else:
                _merge(f1 / "marks45.json", {k: blk for k in (*R.MARK_KEYS, "check_marks45")})
        elif mode == "crash" and log:
            with open(log, "ab") as fh:
                fh.write(b"Traceback (most recent call last):\n  File \"x.py\", line 1\nKeyError: 'part'\n")
        return rcs[mode]

    rc = R.main(["--folders", str(f1), "--board", str(board), "--only", step, *extra], runner=runner,
                capturer=lambda c, e: (0, ""))
    return rc, board, f1, called


@pytest.mark.parametrize("step", list(STEPS))
@pytest.mark.parametrize("mode,rc_expected,done", [("pass", 0, True), ("fail", 0, True), ("notmeasured", 0, True),
                                                  ("crash", 3, False), ("silent", 3, False)])
def test_a_mark_step_is_done_only_with_a_fresh_verdict_and_no_traceback(tmp_path, capsys, step, mode, rc_expected, done):
    rc, board, f1, called = run_step(tmp_path, step, mode)
    out = capsys.readouterr().out
    assert called, "the stub checker was never called"
    assert rc == rc_expected, out
    marker = board / ".release45" / f"{step}.json"
    assert marker.is_file() is done
    if not done:
        assert step in out and "release45.log" in out and "stopped" in out


def test_spread_stops_when_the_source_or_the_key_is_missing(tmp_path):
    f1 = mkfolder(tmp_path, "r1", "model-a")
    board = tmp_path / "board45"
    board.mkdir()
    rel = R.Release([f1], board, R.parse(["--folders", str(f1)]), runner=lambda *a: 0, capturer=lambda *a: (0, ""))
    (f1 / "marks45.json").write_text(json.dumps({"chat_rplong": {"old": 1}}), encoding="utf-8")
    with pytest.raises(R.Stop):                                   # no board file at all
        rel.spread(("chat_rplong",))
    (board / "marks45.json").write_text(json.dumps({"classing": {}}), encoding="utf-8")
    with pytest.raises(R.Stop):                                   # file without the key: the stale block stays unrelayed
        rel.spread(("chat_rplong",))
    assert json.loads((f1 / "marks45.json").read_text(encoding="utf-8"))["chat_rplong"] == {"old": 1}


# ------------------------------------------------------------------ G7
def _assistant_cmd(tmp, anchors=(), extra=(), reference=None):
    f1 = mkfolder(tmp, "r1", "qwen3.8-27b-q8-ref")
    anchor_dirs = [mkfolder(tmp, a, lab) for a, lab in anchors]
    seen = []

    def runner(cmd, env, log):
        if len(cmd) > 1 and Path(cmd[1]).name == "check_assistant_marks.py":
            seen.append([str(c) for c in cmd])
            Path(cmd[cmd.index("--out") + 1]).write_text("{}", encoding="utf-8")
        return 0
    argv = ["--folders", str(f1), "--board", str(tmp / "board45"), "--only", "c-assistant", *extra]
    if anchor_dirs:
        argv += ["--anchors", *map(str, anchor_dirs)]
    if reference:
        argv += ["--reference", reference]
    rc = R.main(argv, runner=runner, capturer=lambda c, e: (0, ""))
    return rc, seen, f1, anchor_dirs


def _identity():
    import check_marks45 as CM
    m = CM._load_marks()
    assert "EV3_identity" in m["evaluation"]["body"]
    return m["segments"]["body"]["pair_rule"]["models"]


def test_c_assistant_gets_the_reference_and_damaged_identity_from_the_addendum(tmp_path):
    rc, seen, f1, _ = _assistant_cmd(tmp_path)
    assert rc == 0 and len(seen) == 1
    sets = [seen[0][i + 1] for i, a in enumerate(seen[0]) if a == "--set"]
    models = _identity()
    assert f"reference={models['reference']}" in sets and f"damaged={models['damaged']}" in sets
    assert models["reference"] == "qwen3.8-27b-q8-ref"        # what the addendum names today, read by code


def test_c_assistant_reference_flag_overrides_the_label(tmp_path):
    rc, seen, f1, _ = _assistant_cmd(tmp_path, reference="my-ref*")
    sets = [seen[0][i + 1] for i, a in enumerate(seen[0]) if a == "--set"]
    assert "reference=my-ref*" in sets


def test_an_anchor_with_a_launch_label_is_refused(tmp_path, capsys):
    rc, seen, *_ = _assistant_cmd(tmp_path, anchors=[("an", "qwen3.8-27b-q8-ref")])
    assert rc == 2 and not seen
    assert "launch" in capsys.readouterr().out


def test_a_multi_model_anchor_folder_points_at_the_split_helper(tmp_path, capsys):
    f1 = mkfolder(tmp_path, "r1", "qwen3.8-27b-q8-ref")
    multi = mkfolder(tmp_path, "multi", "darker-scarlett-27b")
    (multi / "transcripts_deepseek-r1-0528-8b.jsonl").write_text(ROW + "\n", encoding="utf-8")
    rc = R.main(["--folders", str(f1), "--board", str(tmp_path / "board45"), "--anchors", str(multi), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 2 and "split_run_folder45.py" in out


def test_assistant_runs_are_appended_and_deduplicated(tmp_path):
    ex = mkfolder(tmp_path, "extra1", "deepseek-flash")
    rc, seen, f1, anchors = _assistant_cmd(tmp_path, anchors=[("an", "darker-scarlett-27b")],
                                           extra=["--assistant-runs", str(ex), str(tmp_path / "an")])
    assert rc == 0
    cmd = seen[0]
    assert cmd.count(str(ex)) == 1 and cmd.count(str(tmp_path / "an")) == 1


# ------------------------------------------------------------------ the damaged role stays unresolved
def test_a_missing_damaged_model_is_not_evaluable_and_never_passes(tmp_path):
    marks = json.loads(CA.DEFAULT_MARKS.read_text(encoding="utf-8"))
    roles = {k: list(v) for k, v in marks["set"].items()}
    roles["reference"] = ["qwen3.8-27b-q8-ref"]
    block = {"status": "ok", "share": 0.95, "cut": 0, "expected_runs": 30, "items": {"I1": 1.0}}
    runs = {"qwen3.8-27b-q8-ref": [dict(block), dict(block)], "deepseek-flash": [dict(block)],
            "darker-scarlett-27b": [dict(block)], "deepseek-r1-0528-8b": [dict(block, share=0.4)]}
    ok, lines = CA.check(runs, marks, roles)
    text = "\n".join(lines)
    assert ok is False
    assert "not evaluable: no run of the damaged model exists" in text
    assert "(a)" in text and "(d)" in text                      # the marks that do not need it are still evaluated
    assert CA.reasons(lines) == ["damaged-model set not run"]


# ------------------------------------------------------------------ the split helper
def test_split_helper_copies_each_model_byte_for_byte(tmp_path):
    import split_run_folder45 as S
    src = tmp_path / "multi"
    src.mkdir()
    a_lines = ['{"case_id": "A", "r": 1}', '{"case_id": "B",  "r":2}']
    (src / "transcripts_m-a.jsonl").write_bytes(("\n".join(a_lines) + "\n").encode())
    (src / "transcripts_m-b.jsonl").write_bytes(b'{"case_id": "C"}\n')
    (src / "meta_m-a.json").write_text("{}", encoding="utf-8")
    (src / "meta_m-ghost.json").write_text("{}", encoding="utf-8")        # a model that never produced rows
    dest = tmp_path / "out"
    made = S.split(src, dest)
    assert sorted(p.name for p in made) == ["m-a", "m-b"]
    assert (dest / "m-a" / "transcripts_m-a.jsonl").read_bytes() == (src / "transcripts_m-a.jsonl").read_bytes()
    assert (dest / "m-a" / "meta_m-a.json").is_file() and not (dest / "m-b" / "meta_m-b.json").exists()
    assert not (dest / "m-ghost").exists()
    only = S.split(src, tmp_path / "out2", models=["m-b"])
    assert [p.name for p in only] == ["m-b"]
    with pytest.raises(SystemExit):                                       # never writes over an existing copy
        S.split(src, dest)
