"""What the service runs."""
from .app import make_app


def build(store: dict):
    return make_app(store)
