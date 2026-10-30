"""Client event-loop clock selection; keep timers precise on older Windows Python."""
import asyncio
import os
import time


def run(coroutine):
    if os.name == 'nt' and time.get_clock_info('monotonic').resolution > 0.001:
        class PreciseProactorLoop(asyncio.ProactorEventLoop):
            def __init__(self):
                super().__init__()
                # asyncio uses this resolution to decide how early scheduled
                # callbacks may run. GetTickCount64's 15.625 ms resolution can
                # turn a 10 ms sleep into a busy loop on Python 3.12/Windows.
                # Pair the finer clock with its actual resolution; changing
                # only the advertised resolution would retain a coarse clock.
                self._clock_resolution = time.get_clock_info('perf_counter').resolution

            def time(self):
                return time.perf_counter()

        with asyncio.Runner(loop_factory=PreciseProactorLoop) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)
