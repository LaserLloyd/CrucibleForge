"""What the service runs."""
from .app import make_app
from .ratelimit import RateLimit


def build(store: dict):
    return RateLimit(make_app(store), rate=5, burst=10)
