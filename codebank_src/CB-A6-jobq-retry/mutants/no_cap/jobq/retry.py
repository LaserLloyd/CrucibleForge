"""Retrying flaky async calls."""
import asyncio


class RetryError(Exception):
    def __init__(self, attempts: int):
        super().__init__(f"gave up after {attempts} attempts")
        self.attempts = attempts


async def run_with_retry(fn, *, retries=3, retry_on=(ConnectionError, TimeoutError),
                         base_delay=0.5, max_delay=8.0, sleep=asyncio.sleep, jitter=None):
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except retry_on as exc:
            if attempt > retries:
                raise RetryError(attempt) from exc
            delay = base_delay * 2 ** (attempt - 1)
            if jitter is not None:
                delay = jitter(delay)
        await sleep(delay)
