"""Measure a real file over the existing Client API, without changing transport policy.

Management SSH/USB only launches this script and retrieves JSON. File bytes and
latency probes travel through the overlay. Outputs may contain private filenames.
"""
import argparse
import asyncio
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import statistics
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from overlay.config import load as load_config
from aioquic.quic.logger import QuicLogger, QuicLoggerTrace
from overlay.client import Client
from overlay.secure import MAX_RELIABLE_PAYLOAD
import overlay.secure as secure
from overlay.runtime import run as run_async


class CountTrace(QuicLoggerTrace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.counts = Counter()
        self.latest = {}

    def log_event(self, *, category, event, data):
        # Count native QUIC events; retain no packet contents or unbounded qlog.
        self.counts[category + ':' + event] += 1
        if category == 'recovery' and event == 'metrics_updated':
            self.latest.update(data)


class CountLogger(QuicLogger):
    def start_trace(self, is_client, odcid):
        trace = CountTrace(is_client=is_client, odcid=odcid)
        self._traces.append(trace)
        return trace


class MeasuredTransport:
    def __init__(self, transport):
        self.transport = transport
        self.packets = self.bytes = 0

    def sendto(self, data, address):
        self.packets += 1
        self.bytes += len(data)
        self.transport.sendto(data, address)

    def __getattr__(self, name):
        return getattr(self.transport, name)


def distribution(values):
    if not values:
        return None
    values = sorted(values)
    return {'count': len(values), 'mean': statistics.mean(values),
            'median': statistics.median(values),
            'p95': values[min(len(values)-1, int(.95 * (len(values)-1)))],
            'max': values[-1]}


async def experiment(args):
    config = load_config(args.config)
    logger = CountLogger()
    configuration = secure.QuicConfiguration
    secure.QuicConfiguration = lambda **kw: configuration(quic_logger=logger, **kw)
    result = {'role': args.role, 'mode': args.mode, 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'instrumentation': 'UDP send counters; counting qlog without packet retention',
              'chunk_bytes': MAX_RELIABLE_PAYLOAD - 9}
    client = None
    accepted, completed = asyncio.Event(), asyncio.Event()
    outbound = asyncio.Queue(maxsize=128)
    stream = None
    received = 0
    digest = hashlib.sha256()
    first_data = last_data = None
    gaps, samples, rtts = [], [], []
    probes = {}
    phase = 'idle'
    error = None
    interval_bytes = 0
    transfer_start = None
    submitted_bytes = 0

    def receive(data, path):
        nonlocal stream, received, first_data, last_data, error, interval_bytes
        try:
            kind, body = data[:1], data[1:]
            if kind == b'P':
                client.send(b'Q' + body, reliable=False)
            elif kind == b'Q' and args.role == 'send':
                entry = probes.pop(body, None)
                if entry:
                    sent, sent_phase = entry
                    rtts.append({'phase': sent_phase, 'rtt_ms': (time.perf_counter()-sent)*1000,
                                 'path': path, 'elapsed': time.monotonic()-started})
            elif kind == b'S' and args.role == 'receive':
                if stream is not None:
                    raise ValueError('Second file header')
                metadata = json.loads(body)
                result['source'] = metadata
                # Explicit destination; a peer-supplied filename is never a path.
                stream = Path(args.file).open('xb')
                outbound.put_nowait(b'A')
            elif kind == b'A' and args.role == 'send':
                accepted.set()
            elif kind == b'D' and args.role == 'receive':
                offset = int.from_bytes(body[:8], 'big')
                if stream is None or offset != received:
                    raise ValueError('Unexpected file offset')
                now = time.perf_counter()
                if first_data is None:
                    first_data = now
                elif last_data is not None:
                    gaps.append((now-last_data)*1000)
                last_data = now
                payload = body[8:]
                stream.write(payload)
                digest.update(payload)
                received += len(payload)
                interval_bytes += len(payload)
            elif kind == b'E' and args.role == 'receive':
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()
                stream = None
                expected = result['source']
                elapsed = time.perf_counter()-first_data if first_data is not None else 0
                result.update(bytes=received, sha256=digest.hexdigest(),
                              integrity_ok=received == expected['bytes'] and digest.hexdigest() == expected['sha256'],
                              receive_seconds=elapsed, receive_mbps=received*8/elapsed/1e6 if elapsed else 0,
                              delivery_gap_ms=distribution(gaps), delivery_stalls_over_1s=sum(g>1000 for g in gaps))
                outbound.put_nowait(b'C' + json.dumps({'bytes': received, 'sha256': digest.hexdigest(),
                                                    'integrity_ok': result['integrity_ok']}).encode())
                completed.set()
            elif kind == b'C' and args.role == 'send':
                result['receiver_confirmation'] = json.loads(body)
                result['confirmed_seconds'] = time.perf_counter()-transfer_start
                result['confirmed_mbps'] = result['bytes']*8/result['confirmed_seconds']/1e6
                completed.set()
        except Exception as exc:
            error = str(exc)
            completed.set()

    async def reliable(data):
        if error:
            raise ValueError(error)
        remaining = args.timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError('File submission deadline exceeded')
        await asyncio.wait_for(client.send_reliable(data), remaining)

    async def responses():
        while True:
            await reliable(await outbound.get())

    async def ping():
        number = 0
        while True:
            body = struct.pack('!Q', number)
            number += 1
            if client.send(b'P'+body, reliable=False):
                probes[body] = (time.perf_counter(), phase)
            await asyncio.sleep(.25)

    async def sample():
        nonlocal interval_bytes
        previous = time.perf_counter()
        while True:
            await asyncio.sleep(1)
            now = time.perf_counter()
            samples.append({'elapsed': time.monotonic()-started, 'bytes': interval_bytes,
                            'mbps': interval_bytes*8/(now-previous)/1e6, 'path': client.path,
                            'pending': client.pending_count, 'submitted_file_bytes': submitted_bytes,
                            'pending_bytes': client.pending_bytes,
                            'quic_lost_packets': sum(t.counts['recovery:packet_lost'] for t in logger._traces),
                            'quic_recovery': [dict(t.latest) for t in logger._traces],
                            'quic_packets_sent': sum(t.counts['transport:packet_sent'] for t in logger._traces)})
            interval_bytes = 0
            previous = now
            print(json.dumps({'event': 'progress', 'role': args.role, 'bytes': received,
                              'submitted_file_bytes': submitted_bytes,
                              'pending': client.pending_count, 'path': client.path,
                              'quic_lost_packets': samples[-1]['quic_lost_packets']}), flush=True)

    async def direct_fault():
        # Controlled application-level receive failure; never edit router/NAT rules.
        while first_data is None:
            await asyncio.sleep(.05)
        await asyncio.sleep(args.fault_after)
        result['injected_fault'] = {'kind': 'discard direct receive packets',
                                    'started_elapsed': time.monotonic()-started,
                                    'path_at_start': client.path,
                                    'requested_seconds': args.fault_duration}
        client.drop_peer_udp = True
        try:
            await asyncio.sleep(args.fault_duration)
        finally:
            client.drop_peer_udp = False
            result['injected_fault']['ended_elapsed'] = time.monotonic()-started

    client = Client(config, args.node, args.peer, args.mode, receive)
    tasks = []
    started = time.monotonic()
    cpu_start = time.process_time()
    try:
        await client.start()
        client.transport = MeasuredTransport(client.transport)
        await asyncio.wait_for(client.ready.wait(), 90)
        result['setup_seconds'] = time.monotonic()-started
        tasks = [asyncio.create_task(responses()), asyncio.create_task(sample())]
        if args.role == 'receive' and args.fault_duration:
            tasks.append(asyncio.create_task(direct_fault()))
        if args.role == 'send':
            tasks.append(asyncio.create_task(ping()))
            phase = 'idle'
            await asyncio.sleep(6)
            source = Path(args.file)
            with source.open('rb') as handle:
                source_hash = hashlib.file_digest(handle, 'sha256').hexdigest()
            metadata = {'bytes': source.stat().st_size, 'sha256': source_hash, 'name': source.name}
            result.update(metadata)
            await reliable(b'S'+json.dumps(metadata).encode())
            await asyncio.wait_for(accepted.wait(), 30)
            phase = 'load'
            transfer_start = time.perf_counter()
            offset = 0
            with source.open('rb') as handle:
                chunks = 0
                while payload := handle.read(MAX_RELIABLE_PAYLOAD - 9):
                    await reliable(b'D'+offset.to_bytes(8,'big')+payload)
                    offset += len(payload)
                    submitted_bytes = offset
                    # Yield to datagram callbacks even on an uncongested connection.
                    chunks += 1
                    if chunks % 4 == 0:
                        await asyncio.sleep(0)
            await reliable(b'E')
            await asyncio.wait_for(completed.wait(), args.timeout)
            phase = 'after'
            await asyncio.sleep(3)
        else:
            await asyncio.wait_for(completed.wait(), args.timeout)
            await asyncio.sleep(5)
        if error:
            raise ValueError(error)
        result['success'] = result.get('integrity_ok', result.get('receiver_confirmation', {}).get('integrity_ok', False))
    except Exception as exc:
        result.update(success=False, error=repr(exc))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if stream:
            stream.close()
        result.update(elapsed_seconds=time.monotonic()-started, cpu_seconds=time.process_time()-cpu_start,
                      metrics=client.metrics, udp_sent_packets=getattr(client.transport, 'packets', 0),
                      udp_sent_payload_bytes=getattr(client.transport, 'bytes', 0), pending_at_end=client.pending_count,
                      throughput_samples=samples,
                      quic=[{'events': dict(t.counts), 'latest_recovery': t.latest} for t in logger._traces])
        link = client.link
        result['connection_at_end'] = None if link is None else {
            'available': link.available, 'path': link.path, 'original_cid': link.original_cid.hex(),
            'quic_trace': next((i for i, trace in enumerate(logger._traces)
                                if trace is link.quic._quic_logger), None)}
        if args.role == 'send':
            result['probe_rtt_ms'] = {p: distribution([r['rtt_ms'] for r in rtts if r['phase']==p])
                                      for p in ('idle', 'load', 'after')}
            result['probes_without_response_at_end'] = len(probes)
            result['probe_missing_by_phase'] = dict(Counter(p for _, p in probes.values()))
            with Path(args.output).with_suffix('.rtt.csv').open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=('phase', 'rtt_ms', 'path', 'elapsed'))
                writer.writeheader()
                writer.writerows(rtts)
        Path(args.output).write_text(json.dumps(result, indent=2))
        await client.close()
        secure.QuicConfiguration = configuration
    return result['success']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('send','receive'))
    parser.add_argument('--config', required=True)
    parser.add_argument('--node', required=True)
    parser.add_argument('--peer', required=True)
    parser.add_argument('--file', required=True, help='Source, or a new destination that does not yet exist')
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', choices=('auto','direct-only','relay-only'), default='auto')
    parser.add_argument('--timeout', type=float, default=1200)
    parser.add_argument('--fault-after', type=float, default=12,
                        help='Receiver-only: seconds after first file bytes before injecting a direct receive failure')
    parser.add_argument('--fault-duration', type=float, default=0,
                        help='Receiver-only: discard direct peer UDP for this many seconds; zero disables')
    args = parser.parse_args()
    raise SystemExit(0 if run_async(experiment(args)) else 1)


if __name__ == '__main__':
    main()
