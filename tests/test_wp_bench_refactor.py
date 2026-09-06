"""WP-BENCH (Plan V2) — the studioforge.py lease-client fixes made against
~/.openclaw/workspace/fleet-review/plan-v2/reports/wp-bench-audit.md.

No network, no rig, no model load, no lease: every StudioForge call is
stubbed at the module boundary, exactly like test_revision_3_2.py. This file
covers what that one doesn't:

- FIX-2: the D46 "higher-priority resident" 409 dialect is now RETRYABLE
  (bounded, no retry_after_s needed) instead of instant-fatal, and force is
  still never auto-escalated for it.

Also covers two review-round-1 fixes against
~/.openclaw/workspace/fleet-review/plan-v2/reports/review-wp-bench.md:
- M3: the retry log line names the ACTUAL cause (D46 tier vs a plain
  lease_conflict), not a blanket "D46" for both.
- M4: the resident fast path fetches the live plan once, not twice.

The cli.py fixes (FIX-5 status PIN warning, FIX-6 the V2 run-report write
contract) are covered separately in test_wp_bench_cli_v2.py.
"""
from __future__ import annotations

import pytest

from crucibleforge import providers, studioforge


def _sf_cfg(**extra):
    """Same shape as test_revision_3_2.py's helper of the same purpose —
    duplicated locally so this file has no import-order dependency on it."""
    p = {"type": "studioforge", "base_url": "http://x/v1",
         "headers": {"X-MCP-Pin": "${CRUCIBLEFORGE_TEST_PIN2}"}, **extra}
    return {"providers": {"sf": p}, "defaults": {}, "judge": {"candidates": []},
            "models": []}


# --------------------------------------------------------------- FIX-2: D46

def test_acquire_lease_retries_d46_higher_priority_refusal_until_granted(monkeypatch):
    """The literal server message from wp-bench-audit.md §0: no `pinned`
    substring, no retry_after_s. Must be retried (bounded poll), not raised
    on the first attempt, and never with force=true."""
    d46_body = {
        "detail": "higher-priority model(s) ReadyArt/Dark-Scarlett-27B-v2.0-GGUF/"
                  "Dark-Scarlett-27B-v2.0.i1-Q5_K_M_hb16 (priority 1) are resident on "
                  "CUDA [0, 1, 2, 3]; a lease grant does not outrank the chat or agent "
                  "tier (D46). Pass force=true to evict them anyway, or lease other cards"
    }
    answers = iter([(409, d46_body), (409, d46_body), (200, {"lease_id": "GRANTED"})])
    bodies = []
    sleeps = []

    def mgmt(method, b, k, h, path, json=None, timeout=30):
        bodies.append(dict(json))
        return next(answers)

    monkeypatch.setattr(studioforge, "_mgmt", mgmt)
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: sleeps.append(s))
    lease = studioforge.acquire_lease("http://x/v1", "", {}, [0, 1, 2, 3],
                                      model_ids=["jax-xortron-27b"], wait_busy_s=600)
    assert lease["_lease_id"] == "GRANTED"
    assert len(bodies) == 3
    assert all(b["force"] is False for b in bodies)  # never escalated
    assert len(sleeps) == 2 and all(s == studioforge._TIER_REFUSAL_POLL_S for s in sleeps)


def test_acquire_lease_d46_refusal_gives_a_clean_final_error_when_budget_spent(monkeypatch):
    """Bounded — this must still terminate, cleanly, naming the refusal, once
    wait_busy_s is exhausted. Before FIX-2 this raised on attempt #1; after
    FIX-2 it raises after genuinely trying for the whole budget, still never
    forced."""
    d46_body = {"detail": "higher-priority model(s) fam-30b (priority 2) are resident "
                          "on CUDA [0, 1]; a lease grant does not outrank the chat or "
                          "agent tier (D46). Pass force=true to evict them anyway"}
    bodies = []
    sleeps = []
    monkeypatch.setattr(studioforge, "_mgmt",
                        lambda *a, json=None, **k: bodies.append(dict(json)) or (409, d46_body))
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(studioforge.StudioForgeError) as ei:
        studioforge.acquire_lease("http://x/v1", "", {}, [0, 1], model_ids=["m"],
                                  wait_busy_s=125)
    assert ei.value.status == 409
    assert "higher-priority model(s) fam-30b" in str(ei.value)
    assert all(b["force"] is False for b in bodies)  # STILL never escalated
    # bounded: retried until the 125s budget ran out (60s + 60s + <=5s remainder),
    # not forever, and not zero times
    assert 1 <= len(sleeps) <= 3
    assert sum(sleeps) <= 125


def test_acquire_lease_recognises_lease_conflict_code_with_no_retry_after(monkeypatch):
    """The other half of the D46 dialect the audit names: a structured
    ``code: lease_conflict`` with no retry_after_s must ALSO be retried, not
    just the "higher-priority model" message text."""
    body = {"code": "lease_conflict", "detail": "card leased by another holder"}
    answers = iter([(409, body), (200, {"lease_id": "OK"})])
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: next(answers))
    sleeps = []
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: sleeps.append(s))
    lease = studioforge.acquire_lease("http://x/v1", "", {}, [0], model_ids=["m"],
                                      wait_busy_s=90)
    assert lease["_lease_id"] == "OK" and sleeps == [studioforge._TIER_REFUSAL_POLL_S]


def test_acquire_lease_honours_retry_after_over_the_d46_poll_cadence(monkeypatch):
    """A message that matches the D46 markers AND carries a real
    retry_after_s (a future server dialect) must still prefer the concrete
    hint over the generic 60s poll — _retry_wait already does the right
    thing; this just proves the D46 branch does not override it."""
    body = {"detail": "does not outrank the chat tier", "_retry_after_s": 7}
    answers = iter([(409, body), (200, {"lease_id": "OK2"})])
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: next(answers))
    sleeps = []
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: sleeps.append(s))
    lease = studioforge.acquire_lease("http://x/v1", "", {}, [0], model_ids=["m"],
                                      wait_busy_s=90)
    assert lease["_lease_id"] == "OK2" and sleeps == [7.0]


def test_acquire_lease_unrelated_409_is_still_immediately_final(monkeypatch):
    """A 409 that matches NEITHER dialect (pinned, nor D46/lease_conflict)
    must still fail on the first attempt — the new tier-poll must not widen
    into a general "retry every 409" behaviour."""
    calls = []
    monkeypatch.setattr(studioforge, "_mgmt",
                        lambda *a, **k: calls.append(1) or (409, {"detail": "malformed request"}))
    with pytest.raises(studioforge.StudioForgeError):
        studioforge.acquire_lease("http://x/v1", "", {}, [0], model_ids=["m"], wait_busy_s=90)
    assert calls == [1]


# ---------------------------------------------- review round 1: M3, M4

def test_tier_refusal_reason_distinguishes_d46_from_lease_conflict():
    """WP-BENCH review M3: the retry log line must name the ACTUAL cause —
    a genuine D46 priority-tier refusal is a different situation from
    another StudioForge client's lease_conflict sitting on one of these
    cards (observed in practice as a foreign holder, e.g. a captioner
    client, not a priority tier at all), and the audit's own
    failure-triage table depends on an operator telling them apart from
    the log. Checking the D46 message markers before the structured code
    matters too: a future server could plausibly send both on a genuine
    tier refusal, and the message is the more specific signal."""
    d46 = studioforge._tier_refusal_reason(
        409, {}, "higher-priority model(s) fam-30b (priority 1) are resident on "
                "CUDA [0, 1]; a lease grant does not outrank the chat or agent tier (D46)")
    assert d46 is not None and "D46" in d46 and "lease_conflict" not in d46

    conflict = studioforge._tier_refusal_reason(
        409, {"code": "lease_conflict"},
        "lease 9f502a16fb45 (clawforge2) holds CUDA [1]")
    assert conflict is not None and "lease_conflict" in conflict and "D46" not in conflict

    both = studioforge._tier_refusal_reason(
        409, {"code": "lease_conflict"},
        "higher-priority model(s) fam-30b are resident; does not outrank the chat tier")
    assert both is not None and "D46" in both  # message wins when both are present

    neither = studioforge._tier_refusal_reason(409, {}, "malformed request")
    assert neither is None
    assert studioforge._tier_refusal_reason(503, {}, "does not outrank the chat tier") is None


def test_acquire_lease_logs_the_lease_conflict_cause_not_d46(monkeypatch, caplog):
    """End-to-end through acquire_lease's own retry loop: a plain
    lease_conflict refusal must not be misreported as a D46 tier hit in the
    WARNING line an operator actually reads."""
    body = {"code": "lease_conflict", "detail": "lease 9f502a16fb45 (clawforge2) holds CUDA [1]"}
    answers = iter([(409, body), (200, {"lease_id": "OK"})])
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: next(answers))
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: None)
    with caplog.at_level("WARNING", logger="crucibleforge.studioforge"):
        studioforge.acquire_lease("http://x/v1", "", {}, [0], model_ids=["m"], wait_busy_s=90)
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("lease_conflict" in w and "D46" not in w for w in warnings)


def test_resident_fast_path_fetches_loaded_plan_only_once(monkeypatch):
    """WP-BENCH review M4: the resident fast path used to fetch the live
    plan once for its own ready-check and again inside _remember_plan — two
    GET /api/status round trips for one switch_model call. It must now
    reuse the first fetch."""
    calls = []

    def fake_loaded_plan(mid, *a, **k):
        calls.append(mid)
        return {"state": "ready", "ctx_size": 262144, "parallel": 2, "devices": [0, 1]}
    monkeypatch.setattr(studioforge, "loaded_plan", fake_loaded_plan)
    p = providers.get_provider(_sf_cfg(lease=True, lease_devices=[0, 1]), "sf")
    assert p.switch_model("m", 32768) == 0.0
    assert calls == ["m"]  # exactly one call, not two
    assert p.loaded_plan_for("m")["ctx_size"] == 262144  # _remember_plan still ran

