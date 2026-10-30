"""Local proxy dispatch overhead only; this does not measure network throughput.

Run: python -m scripts.benchmark_proxy_dispatch
"""
import asyncio
import statistics
import time
from types import SimpleNamespace

from overlay.proxy import DATA, Proxy, frame


async def benchmark(records=20000, repetitions=5):
    async def send_reliable(payload, stream=None):
        pass

    def send(payload, reliable=None, stream=None):
        return True

    channel = SimpleNamespace(closed=False, allocation_id='test', key=b'x' * 16)
    proxy = Proxy.__new__(Proxy)
    proxy.client = SimpleNamespace(allocation_id='test', send=send,
                                   send_reliable=send_reliable)
    body = b'x' * 16365

    async def previous():
        if channel.closed or channel.allocation_id != proxy.client.allocation_id:
            raise ConnectionError('Channel ended')
        await asyncio.wait_for(proxy.client.send_reliable(
            frame(DATA, channel.key, body), stream=channel.key), 30)

    async def current():
        await proxy.send(channel, DATA, body)

    samples = {'previous': [], 'current': []}
    for repetition in range(repetitions):
        cases = [('previous', previous), ('current', current)]
        if repetition % 2:
            cases.reverse()
        for name, dispatch in cases:
            started = time.perf_counter()
            for _ in range(records):
                await dispatch()
            samples[name].append(time.perf_counter() - started)
    for name, values in samples.items():
        print(f'{name}: {records} records, median {statistics.median(values):.6f} s')
    print('No sockets, encryption, congestion or backpressure in this benchmark.')


if __name__ == '__main__':
    asyncio.run(benchmark())
