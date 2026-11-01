import json

from _w import Clock, call, counting_app, hdr
from porch.ratelimit import RateLimit


def test_burst_then_429_and_refill():
    inner, calls = counting_app()
    clock = Clock()
    app = RateLimit(inner, rate=1, burst=3, clock=clock)
    got = [call(app, "/api/items", key="a") for _ in range(3)]
    assert [g[0] for g in got] == ["200 OK"] * 3
    assert [hdr(g[1], "X-RateLimit-Remaining") for g in got] == ["2", "1", "0"]
    status, headers, body = call(app, "/api/items", key="a")
    assert status == "429 Too Many Requests" and json.loads(body) == {"error": "rate limited"}
    assert hdr(headers, "Content-Type") == "application/json"
    assert len(calls) == 3
    clock.t += 1.0
    assert call(app, "/api/items", key="a")[0] == "200 OK"
    assert call(app, "/api/items", key="a")[0].startswith("429")


def test_refill_never_exceeds_burst_and_partial_tokens():
    inner, calls = counting_app()
    clock = Clock()
    app = RateLimit(inner, rate=2, burst=4, clock=clock)
    for _ in range(4):
        call(app, "/api/x", key="k")
    clock.t += 3600
    got = [call(app, "/api/x", key="k")[0] for _ in range(5)]
    assert got == ["200 OK"] * 4 + ["429 Too Many Requests"]
    clock.t += 0.875         # 1.75 tokens
    s, h, _ = call(app, "/api/x", key="k")
    assert s == "200 OK" and hdr(h, "X-RateLimit-Remaining") == "0"
    clock.t += 0.125         # 0.75 + 0.25 = 1.0 token
    assert call(app, "/api/x", key="k")[0] == "200 OK"
