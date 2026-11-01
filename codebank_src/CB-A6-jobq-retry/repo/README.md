# jobq

Small asyncio helpers used by the household sync jobs (photo backup, mail
fetch). Everything is async; time is injected (`sleep=`) so tests run fast.

`run_with_retry(fn, *, retries=3, retry_on=(ConnectionError, TimeoutError),
base_delay=0.5, max_delay=8.0, sleep=asyncio.sleep, jitter=None)`:

* Calls `await fn()` up to `1 + retries` times and returns the first result.
* Only exceptions that are instances of `retry_on` are retried; any other
  exception propagates at once, unchanged.
* Before retry number k (k = 1, 2, ...) it awaits `sleep(d)` with
  d = min(max_delay, base_delay * 2 ** (k - 1)); with a `jitter` function
  the delay is `jitter(d)` instead.
* When every attempt failed it raises `RetryError` whose `attempts` is the
  number of calls made and whose `__cause__` is the last exception.
* Cancellation is never retried or wrapped: if the task is cancelled while
  `fn` runs or while it sleeps, `asyncio.CancelledError` propagates and `fn`
  is not called again.

`JobRunner(limit)`:

* `await runner.run(jobs)` runs `{job_id: coroutine_function}` with at most
  `limit` running at once and returns `{job_id: result or "error: <ExcName>"}`.
* `runner.stop()` (from another task) cancels every job that has not
  finished; `run()` then returns normally with `"cancelled"` for those jobs.
  No job task is left pending after `run()` returns.
