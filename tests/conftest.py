"""Shared fixtures.

The one interesting thing here is the sandbox opt-in below.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
