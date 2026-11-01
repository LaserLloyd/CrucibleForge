import asyncio

import pytest

from jobq.retry import run_with_retry


def test_cancel_during_sleep():
    calls = []

    async def fn():
        calls.append(1)
        raise ConnectionError("down")

    async def main():
        task = asyncio.create_task(run_with_retry(fn, base_delay=5, sleep=asyncio.sleep))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)

    asyncio.run(main())
    assert calls == [1]


def test_cancel_while_fn_runs():
    calls = []

    async def fn():
        calls.append(1)
        await asyncio.sleep(10)

    async def no_sleep(d):
        pass

    async def main():
        task = asyncio.create_task(run_with_retry(fn, sleep=no_sleep))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert calls == [1]


def test_cancelled_error_raised_by_fn_is_not_retried():
    calls = []

    async def fn():
        calls.append(1)
        raise asyncio.CancelledError()

    async def no_sleep(d):
        pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_with_retry(fn, sleep=no_sleep))
    assert calls == [1]
