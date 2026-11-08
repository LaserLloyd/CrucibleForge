"""The suite must not leak a lease keepalive thread into later tests
(2026-11-08: it resolved 'sf.invalid' during test_case_set_verifies_clean)."""
import threading

from crucibleforge import providers


def test_the_lease_keepalive_is_a_no_op_under_test():
    p = providers.Provider(name="t", type="studioforge", base_url="http://sf.invalid/v1")
    p.lease_idle_ttl_s = 3600
    p._start_keepalive()
    assert p._keepalive is None
    assert not [t for t in threading.enumerate() if t.name == "crucibleforge-lease-keepalive"]
