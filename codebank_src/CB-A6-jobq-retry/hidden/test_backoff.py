import asyncio

import pytest

from jobq.retry import RetryError, run_with_retry


def _always(exc):
    calls = []

    async def fn():
        calls.append(1)
        raise exc
    return fn, calls


def _rec():
    slept = []

    async def sleep(d):
        slept.append(d)
    return sleep, slept


def test_delays_and_give_up():
    fn, calls = _always(TimeoutError("slow"))
    sleep, slept = _rec()
    with pytest.raises(RetryError) as ei:
        asyncio.run(run_with_retry(fn, sleep=sleep))
    assert slept == [0.5, 1.0, 2.0] and len(calls) == 4
    assert ei.value.attempts == 4 and isinstance(ei.value.__cause__, TimeoutError)


def test_cap_and_zero_retries():
    fn, calls = _always(ConnectionError())
    sleep, slept = _rec()
    with pytest.raises(RetryError):
        asyncio.run(run_with_retry(fn, retries=7, base_delay=1, max_delay=8.0, sleep=sleep))
    assert slept == [1, 2, 4, 8.0, 8.0, 8.0, 8.0]
    fn, calls = _always(ConnectionError())
    sleep, slept = _rec()
    with pytest.raises(RetryError) as ei:
        asyncio.run(run_with_retry(fn, retries=0, sleep=sleep))
    assert slept == [] and ei.value.attempts == 1 and len(calls) == 1


def test_jitter_applied_to_capped_delay():
    fn, calls = _always(ConnectionError())
    sleep, slept = _rec()
    with pytest.raises(RetryError):
        asyncio.run(run_with_retry(fn, retries=5, base_delay=2, max_delay=5, sleep=sleep, jitter=lambda d: d + 0.25))
    assert slept == [2.25, 4.25, 5.25, 5.25, 5.25]
