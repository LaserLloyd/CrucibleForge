"""Shared fixtures.

The one interesting thing here is the sandbox opt-in below.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# The suite must NEVER write into the real results/ (a 2026-09-23 test run
# once rewrote the live board). Point the default results dir at a throwaway
# directory BEFORE crucibleforge is imported (config reads the variable at
# import time and load_config() honours it), and check the real dir after.
_REAL_RESULTS = _ROOT / "results"
os.environ["CRUCIBLEFORGE_RESULTS"] = tempfile.mkdtemp(prefix="crucibleforge-test-results-")


def _snapshot(d: Path) -> dict:
    if not d.is_dir():
        return {}
    return {str(p.relative_to(d)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in d.iterdir()}


@pytest.fixture(autouse=True, scope="session")
def _real_results_untouched():
    before = _snapshot(_REAL_RESULTS)
    yield
    after = _snapshot(_REAL_RESULTS)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert not changed, f"the test-suite wrote into the real results/: {changed[:10]}"


@pytest.fixture(autouse=True)
def _allow_unsandboxed_in_tests(monkeypatch, request):
    """Let the suite execute its OWN fixtures where bwrap does not exist.

    Grading refuses to run model-authored code without bubblewrap, which is
    the whole point of that change — and bwrap is Linux-only, so the macOS leg
    of this project's CI has none. But the code the suite executes is not a
    model's: it is a handful of fizzbuzz-sized snippets written in these test
    files, on a disposable runner. Refusing there would mean the platform that
    most needs the guard is the platform that can no longer test it.

    So the opt-in is granted here, explicitly and per-test, rather than by
    weakening the default. Tests that assert the REFUSAL turn it back off
    themselves (they monkeypatch ALLOW_UNSANDBOXED to False), and this fixture
    is a no-op wherever a real sandbox exists.
    """
    from crucibleforge import graders

    if graders.sandbox_available():
        return
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)
    monkeypatch.setattr(graders, "_warned_no_sandbox", True)  # keep the log quiet
