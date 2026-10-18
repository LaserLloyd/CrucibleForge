"""StudioForge contract tests: pin every StudioForge field CrucibleForge reads.

See tests/contract_studioforge/README.md. The fixtures are derived from this
client's code; a StudioForge change that breaks one must fail HERE, visibly,
instead of silently changing a benchmark run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from _fakesf import FakeStudioForge

from crucibleforge import api, studioforge

FIX = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def body_of(name: str) -> str:
    return json.dumps(fixture(name)["body"])


@pytest.fixture(autouse=True)
def _loopback_no_proxy(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: None)


def v1(sf: FakeStudioForge) -> str:
    return sf.url + "/v1"


# ------------------------------------------------- error envelope (api.py)
class TestErrorClassification:
    def test_gpu_leased_with_a_retry_hint_is_waited_out(self):
        fx = fixture("error_gpu_leased")
        err = api.classify_server_error(fx["http_status"], body_of("error_gpu_leased"),
                                        fx["headers"].get("Retry-After"))
        assert type(err) is api.VramContention
        assert err.retry_after_s == 30.0 and err.status == 507

    def test_gpu_leased_without_any_retry_hint_is_terminal(self):
        """A lease refusal that gives no wait time cannot succeed unchanged."""
        body = json.loads(body_of("error_gpu_leased"))
        del body["error"]["studioforge"]["retry_after_s"]
        err = api.classify_server_error(507, json.dumps(body), None)
        assert isinstance(err, api.VramExhausted)

    def test_retry_after_s_is_read_from_error_studioforge(self):
        _ra, _ = api._parse_error_hints(body_of("error_priority_hold"))
        assert _ra == 7.0

    def test_retry_after_header_is_the_fallback(self):
        body = json.loads(body_of("error_gpu_leased"))
        del body["error"]["studioforge"]["retry_after_s"]
        err = api.classify_server_error(507, json.dumps(body), "45")
        assert type(err) is api.VramContention and err.retry_after_s == 45.0

    def test_priority_hold_names_its_holder_and_is_transient(self):
        fx = fixture("error_priority_hold")
        err = api.classify_server_error(fx["http_status"], body_of("error_priority_hold"), "7")
        assert isinstance(err, api.PriorityHold) and isinstance(err, api.VramContention)
        assert err.model_id == "pub/repo/chat-Q5_K_S" and err.priority == 1
        assert err.retry_after_s == 7.0
        assert api._error_code(body_of("error_priority_hold")) == "priority_hold"

    def test_insufficient_vram_is_terminal_unless_busy_or_hinted(self):
        full = api.classify_server_error(507, body_of("error_insufficient_vram"), None)
        assert isinstance(full, api.VramExhausted)
        assert full.suggestions == [{"mode": "all_gpus", "max_ctx_that_fits": 16384}]
        busy = api.classify_server_error(507, body_of("error_insufficient_vram_busy"), None)
        assert type(busy) is api.VramContention and busy.retry_after_s == 12.0
        assert api._busy_models(body_of("error_insufficient_vram_busy"))[0]["model_id"] \
            == "pub/repo/other-Q4_K_M"

    def test_the_error_code_is_found_on_the_sse_embedded_path_too(self):
        wrapped = "server error: " + body_of("error_priority_hold")
        assert api._error_code(wrapped) == "priority_hold"
        err = api.classify_server_error(None, wrapped, None)
        assert isinstance(err, api.PriorityHold)


# ------------------------------------------------ management API (studioforge.py)
class TestManagementApi:
    def test_retry_after_s_nesting_is_read_from_the_http_body(self):
        fx = fixture("error_priority_hold")
        with FakeStudioForge() as sf:
            sf.route("POST", "/api/leases", fx["http_status"], fx["body"])
            code, data = studioforge._mgmt("POST", v1(sf), "", {}, "/api/leases", json={})
        assert code == 503 and data["_retry_after_s"] == 7.0
        assert studioforge._refusal_reason(data) == \
            "priority hold by pub/repo/chat-Q5_K_S (tier 1)"

    def test_status_loaded_row_fields(self):
        fx = fixture("status")["response"]
        mid = fx["loaded"][0]["model_id"]
        with FakeStudioForge() as sf:
            sf.route("GET", "/api/status", 200, fx)
            rows = studioforge.residents(v1(sf), "")
            plan = studioforge.loaded_plan(mid, v1(sf), "")
            par = studioforge.loaded_parallel(mid, v1(sf), "")
        row = rows[0]
        assert (row["model_id"], row["state"], row["active_requests"], row["loaded_by"],
                row["ttl_s"], row["pinned"]) == (mid, "ready", 1, "crucibleforge", 3600, False)
        assert row["plan"] == {"devices": [0, 1], "ctx_size": 65536, "parallel": 4,
                               "kv_cache_type": "q8_0", "mode": "dual_5090"}
        assert plan["state"] == "ready" and plan["parallel"] == 4 and plan["devices"] == [0, 1]
        assert par == 4

    def test_lease_record_kind_and_holder_family_decide_what_is_foreign(self):
        fx = fixture("leases")
        with FakeStudioForge() as sf:
            sf.route("GET", "/api/leases", 200, fx["response"])
            leases = studioforge.list_leases(v1(sf), "")
        foreign = studioforge.foreign_render_leases(leases, fx["devices_wanted"])
        assert [l["id"] for l in foreign] == fx["expect_foreign_render_ids"]

    def test_family_beats_the_holder_string_for_the_judge_phase(self):
        """crucibleforge-judge is OUR lease by family, whatever the string says."""
        recs = [{"id": "x", "holder": "weird-name", "holder_family": "crucibleforge",
                 "kind": "benchmark", "devices": [0]},
                {"id": "y", "holder": "weird-name", "kind": "render", "devices": [0]}]
        assert [l["id"] for l in studioforge.foreign_render_leases(recs, [0])] == ["y"]

    def test_load_recommended_path_body_and_response(self):
        fx = fixture("load_recommended")
        with FakeStudioForge() as sf:
            sf.route("POST", "/api/models/", 200, fx["response"], exact=False)
            out = studioforge.load_recommended(fx["model_id"], v1(sf), "", fx["args"]["ctx_size"],
                                               prefer_mode=fx["args"]["prefer_mode"],
                                               max_slots=fx["args"]["max_slots"])
        req = sf.requests[0]
        assert req["method"] == "POST" and req["path"] == fx["expect_path"]
        assert req["body"] == fx["expect_request_body"]
        assert out == fx["response"]                     # success carries no _status

    def test_load_recommended_507_carries_status_hints_and_suggestions(self):
        fx = fixture("error_insufficient_vram_busy")
        with FakeStudioForge() as sf:
            sf.route("POST", "/api/models/", fx["http_status"], fx["body"], exact=False)
            res = studioforge.load_recommended("pub/repo/chat-Q5_K_S", v1(sf), "", 32768)
        assert res["_status"] == 507 and res["_retry_after_s"] == 12.0
        assert studioforge._busy_models(res) == ["pub/repo/other-Q4_K_M"]

    def test_reduce_parallel_hint_is_parsed_from_the_507_message(self):
        fx = fixture("error_insufficient_vram")
        res = dict(fx["body"], _status=507)
        assert studioforge._fewer_slots_hint(res) == 7

    def test_load_recommended_404_means_an_older_server(self):
        with FakeStudioForge() as sf:        # no route -> 404
            assert studioforge.load_recommended("a/b/c", v1(sf), "", 8192) is None

    def test_models_additive_fields_and_the_studioforge_block(self):
        fx = fixture("models")["response"]
        with FakeStudioForge() as sf:
            sf.route("GET", "/v1/models", 200, fx)
            ids = studioforge.list_models(v1(sf), "")
            loaded = studioforge.loaded_models(v1(sf), "")
            full = studioforge.list_models_full(v1(sf), "")
        assert ids == ["pub/repo/chat-Q5_K_S", "pub/repo/embed-Q8_0"]
        assert [m["identifier"] for m in loaded] == ["pub/repo/chat-Q5_K_S"]
        assert full[1]["studioforge"]["kind"] == "embedding"

    def test_headers_identify_the_client_and_carry_the_key_and_pin(self):
        with FakeStudioForge() as sf:
            sf.route("GET", "/api/status", 200, {"loaded": []})
            studioforge.status(v1(sf), "test-bearer-key", {"X-MCP-Pin": "1234"})
        h = sf.requests[0]["headers"]
        assert h["Authorization"] == "Bearer test-bearer-key"
        assert h["X-MCP-Pin"] == "1234"
        assert h["X-SF-Client"] == "crucibleforge"


# ----------------------------------------------------------------- leases
class TestAcquireLease:
    def test_request_keys_and_every_accepted_id_spelling(self):
        fx = fixture("lease_create")
        for resp, want in zip(fx["responses_with_an_id"], fx["expect_ids"]):
            with FakeStudioForge() as sf:
                sf.route("POST", "/api/leases", 201, resp)
                lease = studioforge.acquire_lease(v1(sf), "", {}, [0, 1],
                                                  model_ids=["pub/repo/chat-Q5_K_S"])
            assert lease["_lease_id"] == want
            assert sorted(sf.requests[0]["body"]) == sorted(fx["expect_request_keys"])
            assert sf.requests[0]["body"]["holder"] == "crucibleforge"

    def test_priority_hold_is_waited_out_then_granted(self):
        hold = fixture("error_priority_hold")
        with FakeStudioForge() as sf:
            answers = [(hold["http_status"], hold["body"], {}), (201, {"id": "L9"}, {})]
            sf._srv.lookup = lambda m, p: answers.pop(0)
            lease = studioforge.acquire_lease(v1(sf), "", {}, [0], model_ids=["a/b/c"])
        assert lease["_lease_id"] == "L9" and len(sf.requests) == 2

    def test_lease_conflict_has_no_countdown_and_is_polled_then_refused(self):
        fx = fixture("error_lease_conflict")
        with FakeStudioForge() as sf:
            sf.route("POST", "/api/leases", fx["http_status"], fx["body"])
            with pytest.raises(studioforge.StudioForgeError) as ei:
                studioforge.acquire_lease(v1(sf), "", {}, [1], model_ids=["a/b/c"],
                                          wait_busy_s=3)
        assert ei.value.status == 409
        assert len(sf.requests) >= 2                      # it polled before giving up

    def test_a_pinned_resident_refusal_is_final_and_force_is_never_sent(self):
        refusal = {"error": {"code": "pinned", "message":
                             "pinned model(s) x are resident; pass force=true to evict"}}
        with FakeStudioForge() as sf:
            sf.route("POST", "/api/leases", 409, refusal)
            with pytest.raises(studioforge.StudioForgeError):
                studioforge.acquire_lease(v1(sf), "", {}, [0], model_ids=["a/b/c"])
        assert [r["body"]["force"] for r in sf.requests] == [False]
