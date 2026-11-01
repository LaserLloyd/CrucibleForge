import io
from urllib.parse import urlencode


def call(app, method="GET", query="", form=None):
    seen = {}
    body = urlencode(form or {}).encode()

    def sr(status, headers):
        seen["status"] = status

    env = {"REQUEST_METHOD": method, "PATH_INFO": "/", "QUERY_STRING": query,
           "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
    out = b"".join(app(env, sr)).decode()
    return seen["status"], out
