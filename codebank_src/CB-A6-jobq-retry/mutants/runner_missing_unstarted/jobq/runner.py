"""Running many jobs with a concurrency limit."""
import asyncio


class JobRunner:
    def __init__(self, limit: int):
        self.limit = limit
        self._tasks: dict[str, asyncio.Task] = {}

    async def run(self, jobs: dict) -> dict:
        sem = asyncio.Semaphore(self.limit)
        results: dict = {}

        async def one(job_id, fn):
            try:
                async with sem:
                    results[job_id] = await fn()
            except asyncio.CancelledError:
                results[job_id] = "cancelled"
            except Exception as exc:
                results[job_id] = f"error: {type(exc).__name__}"

        self._tasks = {job_id: asyncio.create_task(one(job_id, fn)) for job_id, fn in jobs.items()}
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        return results

    def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
