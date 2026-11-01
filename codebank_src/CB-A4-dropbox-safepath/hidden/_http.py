import io
import json


def call(app, method, uri, body=b""):
    out = {}

    def start_response(status, headers):
        out["status"] = int(status.split()[0])

    env = {"REQUEST_METHOD": method, "REQUEST_URI": uri, "PATH_INFO": uri,
           "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
    data = b"".join(app(env, start_response))
    return out["status"], data


def files_under(p):
    return sorted(x.relative_to(p).as_posix() for x in p.rglob("*"))
