import asyncio

from jobq.retry import run_with_retry


def test_first_try():
    calls = []

    async def fn():
        calls.append(1)
        return 42

    async def no_sleep(d):
        raise AssertionError("slept")

    assert asyncio.run(run_with_retry(fn, sleep=no_sleep)) == 42 and calls == [1]
