# Connection maintenance and scheduling

Data maintenance never awaits WebSocket sends, closes or certificate renewal.
Each data-control WebSocket owns a separate maintenance task: heartbeat every
five seconds, send/close bounded to three seconds, cancellation when the socket
session ends. Certificate renewal and exhausted channel-budget renewal also run
there. A failed native QUIC connection signals a control-close request instead
of awaiting it in the data task.

## Data task wakeups

The data task wakes on received QUIC traffic, new application sends, peer/address
updates, mode changes and actual aioquic timer deadlines. It no longer polls
at 10 ms while idle. A one-second maximum maintenance sleep is a safety check;
earlier pacing, loss-recovery, ACK, probe, registration and health deadlines
still wake it. A 1 ms minimum delay avoids busy loops on already-due timers.
There is no new wire protocol or private library hook.

## Adaptive maintenance

| Mechanism | Policy |
| --- | --- |
| Empty NAT hints | Bootstrap only; seven short gaps at 250 ms, then 1 / 2 / 4 / 8 seconds; stop after peer authentication |
| Selected path nonce probe | Every second while application traffic or native data buffers are active; every three seconds while idle |
| Backup relay probe in auto | Every five seconds, while healthy |
| Failed backup direct path | Outstanding nonce expires after three seconds; retry backoff grows to two then four seconds |
| Standalone periodic QUIC PING | Removed; authenticated nonce probes already elicit native ACKs and confirm reachability |
| UDP server registration | About one second until replies arrive, five seconds while replies remain recent; capped at one third of the server TTL |
| Data-control heartbeat | Five seconds, isolated from the data scheduler |

NAT hints are unauthenticated and never prove reachability. Once authenticated,
the direct nonce probes themselves send to the candidate endpoint and maintain
outbound NAT state; extra empty packets are unnecessary even while using relay.
Native QUIC recovery can still send its own probe/PING frames when required.

Application activity includes generic messages, proxy records and application
DATAGRAMs, not path-control/maintenance frames. Unacknowledged native data
buffers also keep the active rate. The selected-path freshness threshold is
three seconds during sustained activity and seven seconds while idle. On
resuming after an idle interval, an immediate probe and a three-second grace
period avoid rejecting a healthy path just because its last idle probe is old.

Nonce replies must still arrive on the same physical carrier within three
seconds. Invalid, late and wrong-carrier replies cannot validate a path. Reducing
backup traffic trades off restoration latency: direct recovery may wait for the
outstanding nonce timeout plus up to four seconds of backoff. Initial direct
success and forced modes retain their existing selection semantics; there is no
new loss/RTT-based ranking or anti-flap guarantee.

Registration replies becoming stale restore the faster registration rate.
Signed peer endpoint updates reset direct probing and bootstrap backoff while
preserving allocation and QUIC state. The server-reported TTL bounds registration
frequency. Control-session loss, directory-session loss, revocation and a new
allocation still close application sockets. Offline authorization leases and
shared device-wide control transport are not introduced by this change.

## Verification and limits

Tests hold a WebSocket heartbeat send blocked while real encrypted QUIC streams
handshake and recover packet loss. Other tests establish that an idle data task
sleeps until an event, empty hints stop after authentication, failed probes back
off, registration adapts and idle traffic does not create periodic QUIC PINGs.
A real encrypted pair also remains connected beyond the configured 10-second
native idle timeout and delivers data afterward without empty NAT hints.
A deterministic 31-second healthy-idle policy simulation permits at most 18
carrier probe requests, versus 62 carrier probes plus 31 standalone PING requests
under the old fixed schedule. These are logical maintenance requests, excluding
responses, ACKs, framing and packet coalescing; this is not a measured bandwidth,
CPU, energy or throughput improvement. Normal loss, multi-stream forwarding,
control revocation and carrier-failure regression tests remain required.
