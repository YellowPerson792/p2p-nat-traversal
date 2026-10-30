# PC-to-phone cellular video transfer, 2026-10-24

The subsequent **direct-only** reverse test completed in **183.99 seconds at
2.52 Mbps**, with independent phone hash verification and zero relayed messages.
Its address-preserving Android route adapter and measurements are recorded in
[the reverse direct report](REVERSE_CELLULAR_DIRECT_VIDEO.md).

A complete **57,952,254-byte video (55.27 MiB)** was transferred from the
Windows PC to the phone through the existing reliable QUIC transport in
**relay-only** mode. The phone independently computed SHA-256 and matched the
PC source. No transport window, congestion controller or retransmission policy
was changed.

## Route verification

The phone stayed associated with Wi-Fi for wireless ADB and SSH management. An
Android-side fixed-server bridge bound both its control TCP and data UDP sockets
to the active cellular Internet network. The phone's ordinary default network
remained Wi-Fi; an independent HTTPS probe used a different public exit when
explicitly bound to cellular. The coordinator observed the phone's UDP endpoint
at that cellular exit, and a server TCP socket snapshot independently confirmed
its TLS control connection came from the same cellular exit. The PC used home
Wi-Fi. No file payload was carried through ADB or SSH.

This route uses the temporary bridge described in
[PHONE_NETWORK_ROUTING.md](../../testing/PHONE_NETWORK_ROUTING.md). Its local copy/forwarding
cost is an additional variable relative to earlier phone-to-PC runs without
this bridge. This single run is not an isolated measurement of direction alone
or a measurement of raw cellular download capacity.

## Complete-file results

| Metric | Result |
|---|---:|
| Sender first submission to validated receiver confirmation | 219.36 s |
| Mean effective throughput | 2.11 Mbps (0.264 MB/s) |
| Peak receiver one-second goodput | 5.96 Mbps |
| Idle probe RTT median / P95 | 138.7 / 273.0 ms |
| Loaded probe RTT median / P95 | 180.5 / 359.9 ms |
| Maximum loaded probe RTT | 608.5 ms |
| Missing loaded probe replies | 4 / 877 |
| Maximum application delivery gap | 549.0 ms |
| Application delivery gaps over one second | 0 |
| Sender cross-connection logical replays | 0 |
| Receiver duplicate logical messages | 0 |
| Invalid packets at receiver | 0 |
| Sender QUIC loss declarations / sent packets | 263 / 54,810 |
| PC sender / Debian receiver CPU seconds | 128.98 / 112.25 |
| Complete-file SHA-256 | match |

The phone copy is visible in Android's **Download** directory as
`overlay-received-from-pc-20261024-231039.mp4`. Its Debian path is
`/mnt/shared/Download/overlay-received-from-pc-20261024-231039.mp4`. The source video was sent unchanged; it was not
re-encoded. The independently verified SHA-256 is:

```text
a1db251abf31d98ee2211695b800216e613efcfe77593f13a4f52405db75cc5e
```

## Interpretation and limits

- Mean throughput of **2.11 Mbps** is close to the first forward cellular relay
  run (**2.04 Mbps**) and below its repeat (**3.82 Mbps**). These runs occurred at
  different times and this reverse run adds local Android forwarding, so they
  do not establish a directional performance ratio.
- The reverse direction is also slow. This weakens an explanation based solely
  on the phone's bulk upload capacity, while leaving ACK return-path quality,
  shared routing, application flow control, congestion recovery and endpoint
  scheduling as possible constraints. It does not eliminate phone uplink effects
  because acknowledgements still use it.
- During file transmission, one-second PC snapshots recorded congestion windows
  between **22,983 and 112,636 bytes**. Thus the transport congestion window itself
  frequently remained below the roughly 114 KB application file window. The
  sender recorded 263 native QUIC loss declarations. These are not a physical
  packet-loss measurement, and one-second samples can miss short extrema.
- The temporary Android bridge forwarded **66,171,428 UDP bytes downlink** and
  **8,398,983 UDP bytes uplink** over the run, including protocol overhead,
  acknowledgements and probes. These are application UDP payload bytes, excluding
  IP/UDP/link headers and TCP control traffic. They are not cellular billing data.
- Probe RTT is a request/reply observation at the PC, not one-way delay. Missing
  probes are omitted from latency percentiles. File delivery gap is measured by
  the receiver callback, not packet capture. Timing excludes setup, the idle
  probe phase and source hashing, and includes receiver flush/fsync and its
  completion reply. All Mbps/MB/s figures use decimal units.
- The bridge was stopped after the run and its final log confirmed the Android
  default network was unchanged. No experiment process is left running.

## Evidence

Ignored `artifacts/phone-video-reverse-cellular-20261024-231039/` contains sender
and receiver JSON, PC RTT CSV, both progress logs, route probes, server TCP peers,
source/destination hash validation, bridge counters and helper source snapshots.
The completed video remains on the phone; it was not copied back over management
SSH for validation. The earlier local PC copy served as the source.
