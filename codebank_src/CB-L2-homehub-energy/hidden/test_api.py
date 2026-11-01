import io
import json

from _e import T
from homehub.api import make_app
from homehub.config import Config
from homehub.hub import Hub


def _get(app, path, query):
    out = {}
    env = {"PATH_INFO": path, "QUERY_STRING": query, "REQUEST_METHOD": "GET", "wsgi.input": io.BytesIO()}
    body = b"".join(app(env, lambda s, h: out.setdefault("s", s)))
    return out["s"], json.loads(body)


def test_daily_chart_after_local_midnight():
    hub = Hub(Config({"device": [{"id": "tv", "kind": "plug"}],
                      "tariff": {"base": 20, "peak": 40, "peak_windows": ["17:00-21:00"]}}))
    tv = hub.devices["tv"]
    tv.meter(0, at=T("2026-01-15T16:00"))         # 17:00 Berlin
    tv.meter(1000, at=T("2026-01-15T17:00"))      # 18:00: 1000 Wh at peak
    tv.meter(1600, at=T("2026-01-15T22:30"))      # 23:30
    tv.meter(2200, at=T("2026-01-15T23:20"))      # 00:20 on the 16th
    app = make_app(hub, now=lambda: T("2026-01-15T23:30"))     # 00:30 Berlin, 16 January
    s, body = _get(app, "/energy/daily", "plug=tv&days=2")
    assert s.startswith("200")
    days = body["days"]
    assert [d["date"] for d in days] == ["2026-01-15", "2026-01-16"]
    d15, d16 = days
    assert d16["wh"] == 240 and d16["cents"] == 5
    assert d15["wh"] == 1960 and d15["cents"] == 66        # 40 + 13.09 + 5.45 + 7.2 = 65.74
