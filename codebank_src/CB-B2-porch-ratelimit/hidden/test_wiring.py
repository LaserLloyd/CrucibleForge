import json

from _w import call, hdr
from porch.server import build


def test_service_is_limited_at_10_then_5_per_second():
    app = build({"k": []})
    got = [call(app, "/api/items", key="k") for _ in range(11)]
    assert [g[0] for g in got[:10]] == ["200 OK"] * 10
    assert got[10][0] == "429 Too Many Requests" and hdr(got[10][1], "Retry-After") == "1"
    assert hdr(got[0][1], "X-RateLimit-Remaining") == "9"
