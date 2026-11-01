"""Per-API-key token bucket rate limiting (docs/ratelimit.md)."""
import json
import math
import time


class RateLimit:
    def __init__(self, app, rate, burst, clock=time.monotonic):
        self.app, self.rate, self.burst, self.clock = app, float(rate), float(burst), clock
        self.buckets: dict[str, tuple[float, float]] = {}

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        key = environ.get("HTTP_X_API_KEY")
        if not path.startswith("/api/") or not key:
            return self.app(environ, start_response)
        now = self.clock()
        tokens, last = self.buckets.get(key, (self.burst, now))
        tokens = tokens + (now - last) * self.rate
        if tokens < 1:
            self.buckets[key] = (tokens, now)
            wait = max(1, math.ceil((1 - tokens) / self.rate))
            body = json.dumps({"error": "rate limited"}).encode()
            start_response("429 Too Many Requests", [("Content-Type", "application/json"),
                                                     ("Content-Length", str(len(body))),
                                                     ("Retry-After", str(wait))])
            return [body]
        tokens -= 1
        self.buckets[key] = (tokens, now)
        remaining = str(math.floor(tokens))

        def sr(status, headers, exc_info=None):
            return start_response(status, list(headers) + [("X-RateLimit-Remaining", remaining)], exc_info) \
                if exc_info else start_response(status, list(headers) + [("X-RateLimit-Remaining", remaining)])

        return self.app(environ, sr)
