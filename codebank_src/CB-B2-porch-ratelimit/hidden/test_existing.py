import json

from _w import call, hdr
from porch.server import build


def test_routes_unchanged():
    app = build({"k": ["eggs"]})
    s, h, b = call(app, "/health")
    assert (s, b, hdr(h, "Content-Type")) == ("200 OK", b"ok", "text/plain")
    s, h, b = call(app, "/api/items", key="k")
    assert s == "200 OK" and json.loads(b) == ["eggs"] and hdr(h, "Cache-Control") == "no-store"
    assert hdr(h, "Content-Length") == str(len(b))
    s, h, b = call(app, "/api/items")
    assert s == "401 Unauthorized"
    s, h, b = call(app, "/api/items", key="nope")
    assert s == "401 Unauthorized"
    s, h, b = call(app, "/api/items", "POST", "k", b'{"n": 2}')
    assert s == "201 Created" and json.loads(b) == {"added": {"n": 2}}
    assert call(app, "/nowhere")[0] == "404 Not Found"
