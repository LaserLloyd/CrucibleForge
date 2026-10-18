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

import _netguard  # noqa: E402  (tests/ is on sys.path as the rootdir conftest dir)

# Hermetic suite: refuse every outbound connect except to ephemeral loopback
# ports a test opens itself -- never the live StudioForge (:1234), a hosted API,
# or a made-up LAN name. Installed before crucibleforge is imported.
_netguard.install()


@pytest.fixture(autouse=True)
def _no_real_network_attempts():
    """Fail the test that attempted a real connection, even when the code under
    test swallowed the refusal (clients here degrade to a sentinel)."""
    _netguard.VIOLATIONS.clear()
    yield
    if _netguard.VIOLATIONS:
        found = list(_netguard.VIOLATIONS)
        _netguard.VIOLATIONS.clear()
        pytest.fail("test attempted a real network connection: " + "; ".join(found),
                    pytrace=False)


@pytest.fixture(autouse=True)
def _no_lease_release_hooks_at_exit(monkeypatch):
    """Tests build providers against made-up hosts ("x", "rig"). A provider that
    takes a lease registers ``release_lease`` with atexit; left in place it fires
    at interpreter exit and tries to DELETE the lease on that host (a DNS lookup
    of "rig" on whatever LAN the developer is on, and noise on a closed stdout).
    Swallow exactly those registrations for the length of the test; everything
    else registered with atexit is passed through untouched.
    """
    import atexit

    real_register = atexit.register

    def register(func, *args, **kwargs):
        if getattr(func, "__name__", "") == "release_lease":
            return func
        return real_register(func, *args, **kwargs)

    monkeypatch.setattr(atexit, "register", register)


@pytest.fixture
def no_management_calls(monkeypatch):
    """Tests that drive the lease/load path with a made-up base_url ("http://x/v1")
    would otherwise reach for the network twice: ``GET /api/leases`` (is a
    foreign render lease on the cards?) and ``POST .../unload`` (re-plan a
    degenerate resident). Answer both here -- no foreign lease, unload fine --
    so the code under test runs without touching a socket. A test that wants
    different answers monkeypatches them itself, after this runs."""
    from crucibleforge import studioforge

    monkeypatch.setattr(studioforge, "vacate_render_leases", lambda *a, **k: False)
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: (200, {}))


# The suite must NEVER write into the real results/ (a 2026-09-23 test run
# once rewrote the live board). Point the default results dir at a throwaway
# directory BEFORE crucibleforge is imported (config reads the variable at
# import time and load_config() honours it), and check the real dir after.
_REAL_RESULTS = _ROOT / "results"
os.environ["CRUCIBLEFORGE_RESULTS"] = tempfile.mkdtemp(prefix="crucibleforge-test-results-")
# ... and see exactly what a clean clone sees: the operator's git-ignored
# private cases/profiles (cases/private/, profiles/private/, *.private.yaml)
# must not change what the suite verifies. Private-category tests build their
# own fixtures (tests/test_private_categories.py).
os.environ["CRUCIBLEFORGE_NO_PRIVATE"] = "1"


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
