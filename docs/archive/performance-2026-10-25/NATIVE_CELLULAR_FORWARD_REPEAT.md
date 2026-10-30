# USB-managed native cellular phone-to-PC repeat, 2026-10-25

The phone Wi-Fi was disabled and management used USB ADB plus its forwarded SSH
port. Android reported virtual tethering upstream `[rmnet16]`. The ordinary
`file_experiment.py` client was launched directly, without `file_via_cellular.py`,
without the route adapter and without an Android UDP bridge process. Both clients
used `direct-only`; the same 57,952,254-byte video was sent unchanged.

| Metric | Result |
|---|---:|
| Complete-file confirmed time | 123.91 s |
| Effective file goodput | 3.74 Mbps (0.468 MB/s) |
| Peak receiver one-second goodput | 6.83 Mbps |
| Loaded RTT median / P95 | 147.9 / 206.6 ms |
| Maximum file delivery gap | 538.3 ms |
| Received direct / relayed application messages | 65,571 / 0 |
| Duplicate messages / logical replays | 0 / 0 |
| Independent source/destination SHA-256 | match |

The previous phone-to-PC run with the optimized bridge delivered 3.62 Mbps in
128.08 seconds; this no-bridge run delivered 3.74 Mbps in 123.91 seconds, about
3.4% higher. Both phone registrations used the same carrier public exit in these
two runs. These are still sequential observations, and disabling Wi-Fi changes
other phone traffic placement, so the difference is not an isolated bridge cost.
The old 6.50 Mbps result was not reproduced even without the bridge. Current data
therefore does not support a large fixed throughput penalty from the bridge or
a stable 6.50 Mbps phone-to-PC rate.

RTT is measured at the phone using request/reply probes and includes endpoint
scheduling. File timing excludes setup, source hashing and idle probes and ends
with the receiver's validated, flushed/fsynced completion reply. These are file
payload rates, not raw cellular link capacity. The complete PC file was hashed
independently and matched the original phone file's independently computed hash:

```text
a1db251abf31d98ee2211695b800216e613efcfe77593f13a4f52405db75cc5e
```

Evidence and the PC received video are retained under ignored
`artifacts/phone-video-native-forward-20261025-001224/`. The topology record contains Wi-Fi disabled, cellular upstream,
USB management and both hash checks. Both test processes exited. Phone Wi-Fi was
left disabled as requested; USB management remains connected.
