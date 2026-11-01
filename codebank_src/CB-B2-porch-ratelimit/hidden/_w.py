import io
import json


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def call(app, path, method="GET", key=None, body=b""):
    seen = {}

    def start_response(status, headers, exc_info=None):
        seen["status"], seen["headers"] = status, list(headers)

    env = {"PATH_INFO": path, "REQUEST_METHOD": method, "CONTENT_LENGTH": str(len(body)),
           "wsgi.input": io.BytesIO(body)}
    if key:
        env["HTTP_X_API_KEY"] = key
    out = b"".join(app(env, start_response))
    return seen["status"], seen["headers"], out


def hdr(headers, name):
    vals = [v for k, v in headers if k.lower() == name.lower()]
    return vals[0] if vals else None


def counting_app():
    calls = []

    def app(environ, start_response):
        calls.append(environ["PATH_INFO"])
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"inner"]
    return app, calls
