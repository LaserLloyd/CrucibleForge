"""scripts/attest_check.py: every NSFW case in a directory carries both attestations (grok + deepseek), PASS."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "attest_check.py"


def load():
    spec = importlib.util.spec_from_file_location("attest_check", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def att(by, verdict="PASS", date="2026-11-11", reasons="ages stated, no real people"):
    return {"by": by, "date": date, "verdict": verdict, "reasons": reasons}


def good(cid="A1"):
    return {"id": cid, "meta": {"attest": [att("grok"), att("deepseek")]}}


def put(d, rel, obj):
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def test_all_attested_is_clean(tmp_path):
    m = load()
    put(tmp_path, "p/set-1.json", {"cases": [good("A1"), good("A2")]})
    put(tmp_path, "lockout.json", {"rungs": [good("L1")]})
    put(tmp_path, "frames.json", {"frames": [good("F1")]})
    assert m.problems(tmp_path) == []


def test_missing_attestation_is_reported(tmp_path):
    m = load()
    put(tmp_path, "set-1.json", {"cases": [{"id": "A1", "meta": {"attest": [att("grok")]}}]})
    out = m.problems(tmp_path)
    assert len(out) == 1 and "A1" in out[0] and "deepseek" in out[0]


def test_fail_verdict_and_missing_meta(tmp_path):
    m = load()
    put(tmp_path, "set-1.json", {"cases": [
        {"id": "A1", "meta": {"attest": [att("grok"), att("deepseek", "FAIL")]}},
        {"id": "A2"},
        {"id": "A3", "meta": {"attest": [att("grok", date=""), att("deepseek")]}}]})
    out = m.problems(tmp_path)
    assert any("A1" in x and "FAIL" in x for x in out)
    assert any("A2" in x for x in out)
    assert any("A3" in x and "date" in x for x in out)


def test_string_ids_refs_and_refs_dirs_are_skipped(tmp_path):
    m = load()
    put(tmp_path, "set-1.json", {"cases": ["XP1-three-rooms", good("A1")]})
    put(tmp_path, "refs/A1.json", {"cases": [{"id": "not-a-case"}]})
    assert m.problems(tmp_path) == []


def test_retired_files_are_skipped(tmp_path):
    m = load()
    put(tmp_path, "new/set-1.json", {"cases": [good("N1")]})
    put(tmp_path, "old/set-2.json", {"cases": [{"id": "OLD1"}]})
    put(tmp_path, "RETIRED.json", {"files": ["old/set-2.json"], "reason": "4.5 drops it"})
    assert m.problems(tmp_path) == []
    put(tmp_path, "old/set-3.json", {"cases": [{"id": "OLD2"}]})
    assert any("OLD2" in x for x in m.problems(tmp_path))


def test_empty_dir_is_a_problem_and_cli_rc(tmp_path):
    m = load()
    assert m.problems(tmp_path)          # nothing to check = not a pass
    put(tmp_path, "set-1.json", {"cases": [good()]})
    ok = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    put(tmp_path, "set-2.json", {"cases": [{"id": "B1"}]})
    bad = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True)
    assert bad.returncode == 1 and "B1" in bad.stdout
