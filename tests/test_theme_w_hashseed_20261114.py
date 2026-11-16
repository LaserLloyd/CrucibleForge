"""theme_w case content must not depend on PYTHONHASHSEED (N2, 2026-11-14). The third-person POV regex was built by
iterating a ``set`` of each cast member's full and first name, so its alternation order (and so the stored case hash)
changed from process to process and a stored row could read as outdated. Build the same cases in fresh interpreters
under different hash seeds and compare a digest of the whole materialized case (checks included)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROG = r"""
import hashlib, json
from crucibleforge.gen import theme_w as tw, theme_rp as tr
out = []
for mod, fam in ((tw, "theme_w"), (tr, "theme_rp")):
    for lv in sorted(mod.LEVELS):
        for i in range(40):
            m = mod.materialize({"type": fam, "level": lv}, release_seed="hs", case_id=f"H{i}")
            out.append(json.dumps(m, sort_keys=True, default=str))
print(hashlib.sha256("\n".join(out).encode()).hexdigest())
"""


def _digest(seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-c", PROG], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout.strip()


def test_case_content_independent_of_hash_seed():
    digests = {s: _digest(s) for s in ("0", "1", "2", "12345")}
    assert len(set(digests.values())) == 1, digests

