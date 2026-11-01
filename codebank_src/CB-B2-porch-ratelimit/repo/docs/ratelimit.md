# Rate limiting (spec)

Add `porch/ratelimit.py` with a WSGI middleware class
`RateLimit(app, rate, burst, clock=time.monotonic)` and make
`porch.server.build(store)` return the app wrapped in
`RateLimit(..., rate=5, burst=10)`.

* Only paths under `/api/` that carry an `X-Api-Key` header are limited.
  Everything else (including `/health`, and `/api/` requests without a key,
  which the app answers with 401) passes through untouched: no extra headers.
* One token bucket per API key. A bucket starts full with `burst` tokens the
  first time its key is seen and refills continuously at `rate` tokens per
  second (as measured by `clock()`, in seconds), never above `burst`.
* A limited request costs one token. If the bucket holds at least one token,
  one is taken, the request goes to the app, and the response gets the extra
  header `X-RateLimit-Remaining: <tokens left, rounded down>`.
* Otherwise the app is NOT called and the answer is
  `429 Too Many Requests` with `Content-Type: application/json`, body
  `{"error": "rate limited"}` and header `Retry-After: <s>`, where s is the
  number of seconds until the bucket holds one token, rounded up to a whole
  number, and at least 1.
* The middleware never changes the app's own status, headers or body.
