from _w import Clock, call, counting_app, hdr
from porch.ratelimit import RateLimit


def test_keys_have_their_own_buckets():
    inner, _ = counting_app()
    app = RateLimit(inner, rate=1, burst=1, clock=Clock())
    assert call(app, "/api/x", key="a")[0] == "200 OK"
    assert call(app, "/api/x", key="a")[0].startswith("429")
    assert call(app, "/api/x", key="b")[0] == "200 OK"


def test_unlimited_paths_untouched():
    inner, calls = counting_app()
    app = RateLimit(inner, rate=1, burst=1, clock=Clock())
    for _ in range(5):
        s, h, b = call(app, "/health", key="a")
        assert s == "200 OK" and hdr(h, "X-RateLimit-Remaining") is None
        s, h, b = call(app, "/api/x")                 # no key
        assert s == "200 OK" and hdr(h, "X-RateLimit-Remaining") is None
        s, h, b = call(app, "/apiary", key="a")
        assert hdr(h, "X-RateLimit-Remaining") is None
    assert len(calls) == 15
