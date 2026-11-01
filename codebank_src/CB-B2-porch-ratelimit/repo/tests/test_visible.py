import io
import json

from porch.server import build


def call(app, path, method="GET", key=None, body=b""):
    seen = {}

    def start_response(status, headers):
        seen["status"], seen["headers"] = status, dict(headers)

    env = {"PATH_INFO": path, "REQUEST_METHOD": method, "CONTENT_LENGTH": str(len(body)),
           "wsgi.input": io.BytesIO(body)}
    if key:
        env["HTTP_X_API_KEY"] = key
    out = b"".join(app(env, start_response))
    return seen["status"], seen["headers"], out


def test_items_roundtrip():
    app = build({"k1": []})
    assert call(app, "/api/items", "POST", "k1", b'"milk"')[0] == "201 Created"
    status, headers, body = call(app, "/api/items", key="k1")
    assert status == "200 OK" and json.loads(body) == ["milk"]
