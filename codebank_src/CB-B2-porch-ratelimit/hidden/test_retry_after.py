from _w import Clock, call, counting_app, hdr
from porch.ratelimit import RateLimit


def test_retry_after_rounds_up():
    inner, _ = counting_app()
    clock = Clock()
    app = RateLimit(inner, rate=0.4, burst=1, clock=clock)
    assert call(app, "/api/x", key="k")[0] == "200 OK"
    s, h, _ = call(app, "/api/x", key="k")
    assert s.startswith("429") and hdr(h, "Retry-After") == "3"       # 1 token needs 2.5 s
    clock.t += 2.0                                                    # 0.8 tokens
    s, h, _ = call(app, "/api/x", key="k")
    assert s.startswith("429") and hdr(h, "Retry-After") == "1"       # 0.5 s -> 1


def test_retry_after_minimum_one_and_long_wait():
    inner, _ = counting_app()
    clock = Clock()
    app = RateLimit(inner, rate=10, burst=1, clock=clock)
    call(app, "/api/x", key="k")
    s, h, _ = call(app, "/api/x", key="k")
    assert hdr(h, "Retry-After") == "1"
    slow = RateLimit(inner, rate=1 / 60, burst=2, clock=clock)
    call(slow, "/api/x", key="z")
    call(slow, "/api/x", key="z")
    s, h, _ = call(slow, "/api/x", key="z")
    assert hdr(h, "Retry-After") == "60"
    clock.t += 30.5
    s, h, _ = call(slow, "/api/x", key="z")
    assert hdr(h, "Retry-After") == "30"
