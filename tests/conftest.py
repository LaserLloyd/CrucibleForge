"""Shared fixtures.

The one interesting thing here is the sandbox opt-in below.
"""
from __future__ import annotations

import os
import re
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
def _no_lease_keepalive_threads(monkeypatch):
    """A provider that takes a lease starts a daemon keepalive thread that
    sleeps >= 30 s, then touches the lease on its (made-up) host.  Left
    running it outlives the test that made it and resolves "sf.invalid" in
    whichever later test is on the clock then -- the netguard blames that
    one (test_case_set_verifies_clean, 2026-11-08).  Tests never need the
    thread: they fake the lease calls themselves."""
    from crucibleforge import providers
    monkeypatch.setattr(providers.Provider, "_start_keepalive", lambda self: None)


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
# ... and the default composition: a CF_COMPOSITION exported in the operator's shell (a board
# render, a run) switched every profile load of the session (2026-11-15, PACKS2: four
# test_main_nsfw_rc2 tests failed under CF_COMPOSITION=rc3-main). A test that wants a
# composition sets it with monkeypatch.setenv.
os.environ.pop("CF_COMPOSITION", None)


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


_PROFILES_38 = _ROOT / "tests" / "fixtures" / "profiles-3.8"


@pytest.fixture
def bench_38(monkeypatch):
    """The frozen suite-3.8.0 benchmark profile (tests/fixtures/profiles-3.8/
    bench.yaml: two headlines, no tiers) in place of profiles/bench.yaml.

    Board, escalation and report tests written against the 3.8 case set
    (RX02, IZ01, ST1, CZ02 in tier 1, Chat/Coding only) test the MECHANISM,
    which 3.9 did not change; they keep their fixtures by running against
    the profile they were written for. 3.9's own profile is tested in
    tests/test_tiers_39.py."""
    from crucibleforge import profiles, report
    monkeypatch.setattr(profiles, "_dirs", lambda cfg=None: [_PROFILES_38])
    monkeypatch.setattr(report, "_PROFILE_LABELS", None)
    # the 4.5 release flip made bench45 the default profile; this directory holds only the frozen `bench`, so the
    # default is pinned back to it, as the archive_default marker does
    monkeypatch.setattr(profiles, "DEFAULT_PROFILE", profiles.ARCHIVE_PROFILE)
    return _PROFILES_38


@pytest.fixture(autouse=True)
def _reset_cases_root():
    """A profile (bench -> archive/4.1.0/cases) selects the case tree process-wide;
    never let one test's profile leak into the next."""
    yield
    from crucibleforge import config, profiles
    config.set_cases_root(_ROOT / "cases")
    profiles.set_active(None)


@pytest.fixture
def cases_41():
    """The 4.1 question tree (archive/4.1.0/cases). Tests that assert the CONTENT of 4.1
    cases (rpdeep structure, RD1/CC3 wording, ladder hashes) read it here, not the live
    cases/ tree, which 4.5 rewrites. The autouse _reset_cases_root restores cases/."""
    from crucibleforge import config
    config.set_cases_root(_ROOT / "archive" / "4.1.0" / "cases")


@pytest.fixture
def legacy_bench45(monkeypatch):
    """bench45 as it stood before WP-4 (8 + hard 2, 29 cases, no ladder): the
    fixtures of the hard-set / scoring tests that predate the v2 composition."""
    from crucibleforge import profiles
    monkeypatch.setattr(profiles, "PROFILES_DIR", _ROOT / "tests" / "fixtures" / "legacy_profiles")


@pytest.fixture(autouse=True)
def _process_switch_env_restored(monkeypatch):
    """--include-staged / --spend-cap-usd travel as environment variables and
    cli._apply_process_switches writes os.environ directly: whatever a test
    leaves there must not reach the next one.

    The originals are recorded THROUGH monkeypatch, before the test runs: its undo
    is last-in-first-out, so these entries are undone LAST, after any
    ``monkeypatch.setenv`` the test itself made. (EXP-20261113-19: a plain
    restore here ran before monkeypatch's undo, and the undo of a test's
    ``setenv(CF_SPEND_CAP_USD, "3")`` wrote back the "15.0" the CLI had put in
    os.environ — arming the spend gate for every later test on that worker.)"""
    import os
    keys = ("CRUCIBLEFORGE_INCLUDE_STAGED", "CF_SPEND_CAP_USD", "CF_SPEND_RUN_CAP_USD", "CF_SPEND_LEDGER",
            "CF_SPEND_CAP_MODEL", "CF_SPEND_GLOBAL_CAP_USD")
    for k in keys:
        v = os.environ.get(k)
        if v is None:
            monkeypatch.setenv(k, "")      # records "absent" for the undo ...
            monkeypatch.delenv(k)          # ... and leaves it absent for the test
        else:
            monkeypatch.setenv(k, v)
    yield

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: minutes on its own (the whole-case-set sandbox verify, full-CLI subprocesses over "
                   "the live ladder); a targeted pre-cut run may deselect with -m 'not slow', the cut tree "
                   "runs them once")
    config.addinivalue_line(
        "markers", "live_ladder: the test reads the live cases/ladder packs "
                   "(otherwise conftest points ladder.LADDER_DIR at an empty dir)")
    config.addinivalue_line(
        "markers", "archive_default: a 4.1 test written when a bare command meant bench; it runs with "
                   "DEFAULT_PROFILE = ARCHIVE_PROFILE (the release flip made bench45 the default)")
    config.addinivalue_line(
        "markers", "shipping_default: the test runs with CF_COMPOSITION unset (bench45's default_composition, "
                   "`standard`); unmarked tests run with CF_COMPOSITION=default unless they set it")
    config.addinivalue_line(
        "markers", "timing: a wall-clock assertion with a generous bound; `pytest -m timing -n 0` runs them alone")


@pytest.fixture(autouse=True)
def _archive_default(request, monkeypatch):
    """4.1 is frozen (ship plan R60): its tests keep their assertions and run as `--profile bench`
    would, by pinning the CLI default back to the archive profile for tests marked archive_default."""
    if request.node.get_closest_marker("archive_default") is not None:
        from crucibleforge import profiles
        monkeypatch.setattr(profiles, "DEFAULT_PROFILE", profiles.ARCHIVE_PROFILE)
    yield


@pytest.fixture(autouse=True, scope="session")
def _base_composition_session():
    """Session half of :func:`_base_composition`: module- and session-scoped fixtures load profiles before any
    function fixture runs, so the base composition is pinned for the whole session when the caller set none."""
    import os
    had = "CF_COMPOSITION" in os.environ
    if not os.environ.get("CF_COMPOSITION"):
        os.environ["CF_COMPOSITION"] = "default"
    yield
    if not had:
        os.environ.pop("CF_COMPOSITION", None)


@pytest.fixture(autouse=True, scope="module")
def _base_composition_module():
    """Module half: a module-scoped fixture (test_core45_composition's ``prof``) loads before any function
    fixture, so a test elsewhere that left the variable unset (2026-11-15: two tests deleted the session pin)
    must not turn the next module's base into `standard`. Re-pins for the module, restores after it."""
    import os
    had = os.environ.get("CF_COMPOSITION")
    if not had:
        os.environ["CF_COMPOSITION"] = "default"
    yield
    if not had:
        os.environ.pop("CF_COMPOSITION", None)
    else:
        os.environ["CF_COMPOSITION"] = had


@pytest.fixture(autouse=True)
def _base_composition(request, monkeypatch):
    """The cut (lead 2026-11-15) made `standard` bench45's default_composition: with CF_COMPOSITION unset a
    bare load is the shipping test. Tests written when unset meant the base composition keep their assertions
    by running with CF_COMPOSITION=default; a test that sets or deletes the variable itself overrides this,
    and tests marked shipping_default see it unset (the shipping default)."""
    import os
    if request.node.get_closest_marker("shipping_default") is not None:
        monkeypatch.delenv("CF_COMPOSITION", raising=False)
    elif not os.environ.get("CF_COMPOSITION"):
        monkeypatch.setenv("CF_COMPOSITION", "default")
    yield


@pytest.fixture(autouse=True)
def _quarantine_store_isolated(monkeypatch, tmp_path_factory):
    """Every test gets its own empty quarantine store. Test runs once left model
    records (``bad``, ``m``) in the operator's store; with one real early stop they
    made three in a row and opened a queue halt that stopped every later run
    (2026-11-21). A test that points the variable elsewhere itself still may."""
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", str(tmp_path_factory.mktemp("quarantine")))
    yield


@pytest.fixture(autouse=True)
def _ladder_dir_isolated(request, monkeypatch, tmp_path_factory):
    """Point ladder.LADDER_DIR at an empty directory unless the test is marked
    ``live_ladder``. A test that set a test release seed while the live ladder was
    in reach materialised every generated pack under that seed -- the combsyn
    level-7 set alone took ~30 minutes (test_gen_registry, 2026-11-14). The private
    ladder is already out of reach (CRUCIBLEFORGE_NO_PRIVATE=1 above), and
    gen.set_release_seed refuses a non-production seed while the live ladder is in
    reach. Session/module fixtures set up before this runs and still see the live
    tree (case_set_check); a test that switches the case root itself
    (config.set_cases_root) gets that tree's ladder, as before."""
    if request.node.get_closest_marker("live_ladder") is None:
        from crucibleforge import ladder
        empty = tmp_path_factory.getbasetemp() / "empty-ladder"
        empty.mkdir(exist_ok=True)
        monkeypatch.setattr(ladder, "LADDER_DIR", empty)
    yield


@pytest.fixture(scope="session")
def case_set_check():
    """verify_cases._check over the whole shipped case set, run ONCE per session.

    Two tests assert the case set is clean (test_v3 and test_simplify_20260923).
    Each used to run every reference solution, mutant and code-bank sandbox on
    its own, one case at a time: about 20 + 16 minutes of a ~55-minute suite,
    on one pinned core (2026-11-13 fan/heat review). The work is subprocess-bound
    (bwrap sandboxes), so a small thread pool spreads it over a few cores; the
    per-case result is identical to the serial loop. CRUCIBLEFORGE_VERIFY_WORKERS=1
    forces the serial path.

    Returns (cases, {id(case dict): errors list | None}); None = Unsupported here.
    """
    from concurrent.futures import ThreadPoolExecutor

    from crucibleforge import config, verify_cases
    from crucibleforge.codebank import Unsupported

    import _staged45     # wired cases only: the staged sets are gated when wired (tests/_staged45.py)
    cases = [c for c in config.load_cases() if not (c.get("ladder_pack") and _staged45.listed(c["ladder_pack"]))]

    def one(c):
        try:
            return verify_cases._check(c)
        except Unsupported:
            return None

    raw = os.environ.get("CRUCIBLEFORGE_VERIFY_WORKERS", "").strip()
    workers = int(raw) if raw.isdigit() and int(raw) > 0 else min(4, max(1, (os.cpu_count() or 2) // 4))
    if workers == 1:
        results = [one(c) for c in cases]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(one, cases))
    # A reference solution's own speed checks ("too slow", a group that timed out) are wall-clock bounds: under a
    # parallel suite on a loaded host they can trip for a correct solution. Such a case is checked once more, alone,
    # after the pool; a wrong answer fails both times, and every other kind of error is kept as found.
    for i, (c, r) in enumerate(zip(cases, results)):
        if r and all(_TIMING_ERR.search(str(e)) for e in r):
            results[i] = one(c)
    return cases, {id(c): r for c, r in zip(cases, results)}


_TIMING_ERR = re.compile(r"too slow|did not run \(timeout|timed out|TimeoutExpired")


@pytest.fixture(autouse=True)
def _speaker_tag_lead_off():
    """session_checks.TAG_LEAD is a process switch set by profiles.apply_profile (4.5 only);
    a test that applied bench45 must not leave it on for the next test."""
    from crucibleforge import session_checks
    yield
    session_checks.set_tag_lead(False)


# --------------------------------------------------------------------------------------------
# Failures that come from the HOST, not from the code under test, become skips with a stated reason.
#
# Three kinds, all explicit and short; a failure that matches none of them still fails:
#   1. a machine without a hardware instruction counter (perf_event_open: GitHub's hosted runners,
#      most VMs): the work clock of the planted-optimisation generator cannot run there;
#   2. Linux hosts whose `awk` is an /etc/alternatives symlink (Debian, Ubuntu): the grading sandbox
#      binds /usr and /bin but not /etc, so `awk` is a dangling link inside it and the shell-script
#      reference solution of CB-E1-rotate-backups prints nothing;
#   3. Windows and macOS: graded code runs in a Linux sandbox (bubblewrap, resource limits, fcntl
#      locks, preexec_fn), none of which exists there.
# The Python version is NOT handled here: it is pinned in .python-version, because a regrade on
# another interpreter is refused by design.
# --------------------------------------------------------------------------------------------
_NO_COUNTER = "no instruction counter (perf_event_open)"
_AWK_CASE_TESTS = ("tests/test_simplify_20260923.py::test_cases_verify_clean",
                   "tests/test_v3.py::test_case_set_verifies_clean")
_NEEDS_LINUX_SANDBOX = (
    "Exception occurred in preexec_fn",
    "preexec_fn is not supported on Windows",
    "refusing to execute model-authored code without a sandbox",
    "fixcheck refuses to run without a working bwrap sandbox",
    "fixcheck needs a POSIX host",
    "the code bank needs a POSIX host",
    "module 'resource' has no attribute",
    "module 'fcntl' has no attribute",
)


def _awk_is_etc_link() -> bool:
    try:
        return os.readlink("/usr/bin/awk").startswith("/etc/")
    except OSError:
        return False


def _host_skip_reason(nodeid: str, text: str, platform: str = sys.platform) -> str | None:
    """The skip reason when a failure (``text`` = its exception lines) is the host's doing, else None."""
    if _NO_COUNTER in text:
        return "no hardware instruction counter on this machine (perf_event_open unavailable)"
    if platform.startswith("linux"):
        if nodeid in _AWK_CASE_TESTS and _awk_is_etc_link():
            return "the sandbox does not bind /etc, so this host's awk (an /etc/alternatives link) is missing in it"
        return None
    if any(s in text for s in _NEEDS_LINUX_SANDBOX):
        return "needs the Linux sandbox"
    if "WinError 32" in text and "bench-probe-" in text:     # the sandbox self-test's temp files
        return "needs the Linux sandbox"
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when not in ("setup", "call") or not rep.failed or call.excinfo is None:
        return
    text = "\n".join(ln for ln in rep.longreprtext.splitlines() if ln.startswith("E"))
    reason = _host_skip_reason(item.nodeid, text)
    if reason:
        rep.outcome = "skipped"
        rep.longrepr = (str(item.path), item.location[1] or 0, f"Skipped: {reason}")
