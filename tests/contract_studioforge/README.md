# StudioForge contract tests (CrucibleForge)

`test_contract_studioforge.py` pins the exact StudioForge behaviour
CrucibleForge depends on, using the small JSON fixtures in `fixtures/`.

**Provenance: every fixture is derived from the client code's expectations**
(what `crucibleforge/api.py` and `crucibleforge/studioforge.py` read and branch
on), not recorded from a live server. If a StudioForge change breaks one of
these shapes, the matching test fails visibly in CrucibleForge's CI, and the
StudioForge change has to be reverted or land together with an updated fixture
and client.

| Fixture | What the client depends on |
|---|---|
| `error_gpu_leased.json` | `error.code == "gpu_leased"`, `error.studioforge.retry_after_s`, `error.studioforge.lease{holder,holder_family,kind}`, `Retry-After` header |
| `error_priority_hold.json` | 503 `priority_hold` with `error.studioforge.priority_hold{model_id,priority}` is transient |
| `error_insufficient_vram.json` | 507 `insufficient_vram` with no `busy_models` and no retry hint is terminal; "reduce parallel from N to M" is parsed |
| `error_insufficient_vram_busy.json` | `error.studioforge.busy_models[]` / `retry_after_s` turn the same 507 into "wait" |
| `error_lease_conflict.json` | 409 `lease_conflict` carries no countdown and is polled |
| `leases.json` | `GET /api/leases -> {"leases":[...]}`; record fields `id`, `holder`, `holder_family`, `kind`, `devices`, `model_ids` |
| `lease_create.json` | `POST /api/leases` body keys; accepted spellings of the lease id |
| `status.json` | `GET /api/status -> loaded[].{model_id,state,active_requests,loaded_by,ttl_s,pinned,plan.*}` |
| `load_recommended.json` | `POST /api/models/{quoted id}/load-recommended` body `{ctx_size, prefer_mode, max_slots}` |
| `models.json` | `GET /v1/models -> data[].id` and the additive `studioforge{state,kind,size_bytes,arch_supported}` block |

Keep the fixtures small; add a field only when the client starts depending on it.
The tests use an in-process fake server on an ephemeral loopback port
(`tests/_fakesf.py`); the suite's socket guard (`tests/_netguard.py`) forbids any
other connection, so they can never reach a real StudioForge.
