import asyncio

from jobq.runner import JobRunner


def test_stop_cancels_and_run_returns():
    async def main():
        runner = JobRunner(2)

        async def quick():
            return "ok"

        async def boom():
            raise KeyError("k")

        async def slow():
            await asyncio.sleep(10)
            return "late"

        jobs = {"a": quick, "b": boom, "c": slow, "d": slow, "e": slow, "f": slow}
        task = asyncio.create_task(runner.run(jobs))
        await asyncio.sleep(0.05)
        runner.stop()
        res = await asyncio.wait_for(task, 2)
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        return res, others

    res, others = asyncio.run(main())
    assert res == {"a": "ok", "b": "error: KeyError", "c": "cancelled", "d": "cancelled",
                   "e": "cancelled", "f": "cancelled"}
    assert others == []


def test_stop_before_any_job_started():
    async def main():
        runner = JobRunner(3)
        ran = []

        async def job():
            ran.append(1)
            return 1

        task = asyncio.create_task(runner.run({"x": job, "y": job}))
        await asyncio.sleep(0)          # run() has created its job tasks; none has started yet
        runner.stop()
        res = await asyncio.wait_for(task, 2)
        return res, ran

    res, ran = asyncio.run(main())
    assert res == {"x": "cancelled", "y": "cancelled"} and ran == []
