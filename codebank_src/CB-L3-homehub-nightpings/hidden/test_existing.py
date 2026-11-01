import io
import json
from datetime import datetime, timezone

from homehub.api import make_app
from homehub.energy import Tariff, daily_costs
from homehub.scheduler import Scheduler
from _night import house

UTC = timezone.utc


def test_energy_and_scheduler():
    T = lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC)
    r = [(T("2026-01-15T15:30"), 0), (T("2026-01-15T16:30"), 1000)]
    assert daily_costs(r, Tariff(20, 40, [__import__("homehub.util.timeparse", fromlist=["Window"]).Window.parse("17:00-21:00")])) \
        == {"2026-01-15": {"wh": 1000, "cents": 30}}
    s = Scheduler()
    s.add("m", "07:00")
    assert len(s.due(T("2026-01-15T00:00"), T("2026-01-17T00:00"))) == 2


def test_api_devices():
    hub = house()
    out = {}
    body = b"".join(make_app(hub)({"PATH_INFO": "/devices", "QUERY_STRING": "", "REQUEST_METHOD": "GET",
                                   "wsgi.input": io.BytesIO()}, lambda s, h: out.setdefault("s", s)))
    assert out["s"].startswith("200") and len(json.loads(body)) == 4
