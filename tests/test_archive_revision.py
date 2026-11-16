"""scripts/archive_revision.py against a synthetic source repo."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "archive_revision.py"

OK_NSFW = [{"id": "N1", "category": "nsfw", "prompt": "Mara (32) and Jonah (35) are adults at dinner."}]
BAD_NSFW = [{"id": "N2", "category": "nsfw", "prompt": "Two people share a kiss."}]


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    *a], check=True, capture_output=True)


def _repo(tmp_path):
    src = tmp_path / "src"
    for rel, content in {
        "cases/math.json": json.dumps([{"id": "M1", "prompt": "2+2"}]),
        "cases/nsfw_ok.json": json.dumps(OK_NSFW),
        "cases/nsfw_bad.json": json.dumps(BAD_NSFW),
        "cases/fixture.json": json.dumps([{"id": "F1", "prompt": "open /home/alex/app/main.py"}]),  # scrub-ok: fixture
        "cases/realpath.json": json.dumps([{"id": "F2", "prompt": "open /home/alex/a and /home/bob/b"}]),  # scrub-ok: fixture
        "cases/private/x.json": "[]",
        "cases/a.local.json": "[]",
        "profiles/bench.yaml": "name: b\n",
        "profiles/bench.private.yaml": "x: 1\n",
        "docs/SCORING.md": "scoring\n",
        "docs/CUSTOM-TESTS.md": "no\n",
    }.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    src.mkdir(exist_ok=True)
    _git(src, "init", "-q")
    _git(src, "add", "-f", ".")
    _git(src, "commit", "-qm", "r1")
    return src


def _run(src, out, version, rev="HEAD"):
    r = subprocess.run([sys.executable, str(SCRIPT), "--rev", rev, "--version", version,
                        "--src", str(src), "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out / version


def test_export_gate_and_same_as(tmp_path):
    src = _repo(tmp_path)
    out = tmp_path / "out"
    d = _run(src, out, "1.0.0")
    m = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))
    assert sorted(m["files"]) == ["cases/fixture.json", "cases/math.json",
                                  "cases/nsfw_ok.json", "docs/SCORING.md", "profiles/bench.yaml"]
    ex = {e["path"]: e["reason"] for e in m["excluded"]}
    assert sorted(ex) == ["cases/nsfw_bad.json", "cases/realpath.json"]
    assert "EL-17" in ex["cases/nsfw_bad.json"] and "scrub_check" in ex["cases/realpath.json"]
    assert m["case_count"] == 3 and not (d / "cases/nsfw_bad.json").exists()
    assert not (d / "cases/private").exists()
    d2 = _run(src, out, "1.0.1")
    m2 = json.loads((d2 / "MANIFEST.json").read_text(encoding="utf-8"))
    assert m2["same_as"] == "1.0.0" and m2["files"] == {}
    assert [p.name for p in d2.iterdir()] == ["MANIFEST.json"]


def test_never_writes_src(tmp_path):
    src = _repo(tmp_path)
    before = subprocess.run(["git", "-C", str(src), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    _run(src, tmp_path / "out", "1.0.0")
    after = subprocess.run(["git", "-C", str(src), "status", "--porcelain"],
                           capture_output=True, text=True).stdout
    assert before == after


def _run_raw(src, out, version, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), "--rev", "HEAD", "--version", version,
                           "--src", str(src), "--out", str(out), *extra],
                          capture_output=True, text=True)


def test_complete_refuses_dirty_gate_without_frozen_for(tmp_path):
    src = _repo(tmp_path)
    out = tmp_path / "out"
    r = _run_raw(src, out, "4.1.0", "--complete")
    assert r.returncode != 0 and "--frozen-for" in r.stderr
    assert not (out / "4.1.0").exists()            # nothing written


def test_complete_frozen_keeps_every_file(tmp_path):
    src = _repo(tmp_path)
    out = tmp_path / "out"
    r = _run_raw(src, out, "4.1.0", "--complete", "--frozen-for", "bench")
    assert r.returncode == 0, r.stderr
    d = out / "4.1.0"
    m = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))
    assert m["excluded"] == [] and m["complete"] is True and m["frozen_for"] == "bench"
    assert (d / "cases/nsfw_bad.json").is_file() and (d / "cases/realpath.json").is_file()
    assert {f["path"] for f in m["gate_findings"]} == {"cases/nsfw_bad.json", "cases/realpath.json"}
    assert m["case_count"] == 5 and "cases/nsfw_bad.json" in m["files"]


def test_complete_clean_gate_needs_no_frozen_for_and_never_same_as(tmp_path):
    src = tmp_path / "src"
    (src / "cases").mkdir(parents=True)
    (src / "cases/m.json").write_text(json.dumps([{"id": "M1", "prompt": "2+2"}]), encoding="utf-8")
    _git(src, "init", "-q"); _git(src, "add", "-f", "."); _git(src, "commit", "-qm", "r1")
    out = tmp_path / "out"
    assert _run_raw(src, out, "1.0.0").returncode == 0
    r = _run_raw(src, out, "1.0.1", "--complete")
    assert r.returncode == 0, r.stderr
    m = json.loads((out / "1.0.1/MANIFEST.json").read_text(encoding="utf-8"))
    assert "same_as" not in m and m["files"] and m["excluded"] == [] and m["frozen_for"] is None
    assert (out / "1.0.1/cases/m.json").is_file()


def test_frozen_for_needs_complete(tmp_path):
    src = _repo(tmp_path)
    r = _run_raw(src, tmp_path / "out", "1.0.0", "--frozen-for", "bench")
    assert r.returncode != 0 and "--complete" in r.stderr
