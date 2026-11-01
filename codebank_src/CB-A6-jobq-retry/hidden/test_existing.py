import asyncio

from jobq.retry import run_with_retry
from jobq.runner import JobRunner


def test_eventual_success_returns_value():
    calls = []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError()
        return {"ok": True}

    async def nosleep(d):
        pass

    assert asyncio.run(run_with_retry(fn, sleep=nosleep)) == {"ok": True} and len(calls) == 3


def test_runner_limit_and_results():
    async def main():
        running = [0, 0]

        def job(i):
            async def fn():
                running[0] += 1
                running[1] = max(running[1], running[0])
                await asyncio.sleep(0.01)
                running[0] -= 1
                if i == 3:
                    raise RuntimeError()
                return i * 10
            return fn

        res = await JobRunner(2).run({f"j{i}": job(i) for i in range(6)})
        return res, running[1]

    res, peak = asyncio.run(main())
    assert res == {"j0": 0, "j1": 10, "j2": 20, "j3": "error: RuntimeError", "j4": 40, "j5": 50}
    assert peak == 2
