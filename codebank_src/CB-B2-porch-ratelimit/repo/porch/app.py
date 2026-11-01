"""The WSGI application."""
import json


def _send(start_response, status, obj, extra=()):
    body = json.dumps(obj).encode()
    start_response(status, [("Content-Type", "application/json"),
                            ("Content-Length", str(len(body))), *extra])
    return [body]


def make_app(store: dict):
    """`store` maps API key -> list of items."""

    def app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")
        if path == "/health":
            start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", "2")])
            return [b"ok"]
        if not path.startswith("/api/"):
            return _send(start_response, "404 Not Found", {"error": "not found"})
        key = environ.get("HTTP_X_API_KEY")
        if not key or key not in store:
            return _send(start_response, "401 Unauthorized", {"error": "unauthorized"})
        if path == "/api/items" and method == "GET":
            return _send(start_response, "200 OK", store[key], [("Cache-Control", "no-store")])
        if path == "/api/items" and method == "POST":
            size = int(environ.get("CONTENT_LENGTH") or 0)
            item = json.loads(environ["wsgi.input"].read(size) or b"null")
            store[key].append(item)
            return _send(start_response, "201 Created", {"added": item})
        return _send(start_response, "404 Not Found", {"error": "not found"})

    return app
