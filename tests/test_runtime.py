import asyncio
import time

from overlay.runtime import run


def test_short_sleeps_do_not_turn_into_busy_polling():
    async def check():
        started = time.perf_counter()
        for _ in range(20):
            await asyncio.sleep(0.003)
        return time.perf_counter() - started

    # Allow a little measurement tolerance while rejecting the Windows coarse
    # clock case where all sleeps can complete immediately inside one tick.
    assert run(check()) >= 0.05
