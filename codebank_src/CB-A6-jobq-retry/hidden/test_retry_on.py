import asyncio

import pytest

from jobq.retry import RetryError, run_with_retry


def _flaky(errors, result="done"):
    calls = []

    async def fn():
        calls.append(1)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return result
    return fn, calls


async def _nosleep(d):
    pass


def test_other_exceptions_propagate_unchanged():
    fn, calls = _flaky([ValueError("bad input")])
    with pytest.raises(ValueError, match="bad input"):
        asyncio.run(run_with_retry(fn, sleep=_nosleep))
    assert len(calls) == 1


def test_keyboard_interrupt_like_base_exceptions_not_retried():
    class Boom(BaseException):
        pass
    fn, calls = _flaky([Boom()])
    with pytest.raises(Boom):
        asyncio.run(run_with_retry(fn, sleep=_nosleep))
    assert len(calls) == 1


def test_custom_retry_on_and_subclasses():
    fn, calls = _flaky([ConnectionResetError(), KeyError("x")])
    with pytest.raises(KeyError):
        asyncio.run(run_with_retry(fn, sleep=_nosleep))
    assert len(calls) == 2
    fn, calls = _flaky([KeyError("x"), KeyError("y")], result=7)
    assert asyncio.run(run_with_retry(fn, retry_on=(KeyError,), sleep=_nosleep)) == 7
    fn, calls = _flaky([ConnectionError()])
    with pytest.raises(ConnectionError):
        asyncio.run(run_with_retry(fn, retry_on=(KeyError,), sleep=_nosleep))
