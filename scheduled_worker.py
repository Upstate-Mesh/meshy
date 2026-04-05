import asyncio
from datetime import datetime

from croniter import croniter
from loguru import logger


class ScheduledWorker:
    def __init__(self, cron_expr, func, *args, **kwargs):
        self.cron_expr = cron_expr
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self._task = None

    async def _runner(self):
        cron = croniter(self.cron_expr, datetime.now())

        while True:
            next_run = cron.get_next(datetime)
            sleep_seconds = (next_run - datetime.now()).total_seconds()

            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)

            try:
                await self.func(*self.args, **self.kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Worker error in {self.func.__name__}: {e}")

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._runner())

    def stop(self):
        if self._task:
            self._task.cancel()
