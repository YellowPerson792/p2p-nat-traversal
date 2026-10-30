# Selecting cellular while the phone stays on Wi-Fi

For the PC overlay, the newer [cellular UDP bridge and TCP proxy](../../scripts/android-cellular/README.md)
supports project UDP/QUIC traffic over USB, plus the TLS control connection,
while the phone stays on Wi-Fi. A separate SOCKS5 mode supports ordinary TCP
applications. Neither mode provides transparent whole-PC IP routing.

The subsequent [archived bridge performance audit](../archive/performance-2026-10-25/BRIDGE_PERFORMANCE.md) records local
forwarding stress tests, a no-bridge cellular control and an optimized full-file
run. It also documents the current packet/buffer reuse changes and remaining limits.

Verified on the Pixel 10 on 2026-10-24 using wireless ADB. The default Android
network remained Wi-Fi throughout. A temporary Android-side helper explicitly
bound its TCP/UDP sockets to the active cellular Internet network using
`android.net.Network.bindSocket()`.

Two independent checks established the route:

- An ordinary HTTPS public-IP request returned the home broadband exit. A request
  explicitly bound to the cellular network returned a different cellular exit.
  The Android default network ID stayed unchanged before and after the requests.
- The existing Debian QUIC client completed **20/20 reliable encrypted echoes**
  through the coordinator, with a mean application RTT of **210.12 ms**. The
  coordinator observed its UDP registration at the cellular exit. A server-side
  TCP socket observation independently showed the phone's TLS control connection
  arriving from that same cellular exit, while the PC arrived from home broadband.

## How the experiment works

The Debian VM normally sends through Android's virtual tethering interface, whose
upstream follows Android's network selection. Binding a Linux socket to the VM's
virtual NIC alone does not choose the Android upstream.

The temporary helper instead listens only on the Android-facing VM gateway:

- UDP is forwarded over a cellular-bound socket to the fixed coordination/relay
  server and port. Only the known VM source address and its first UDP endpoint
  are accepted; it is not a general destination-selectable UDP proxy.
- TCP is forwarded over a cellular-bound socket to the fixed server control port.
  The helper does not terminate TLS.
- A temporary Python route adapter changes the client's local TCP/UDP destination
  to the helper. The original server URL, CA verification, approved peer pin,
  HMAC checks and end-to-end QUIC encryption remain in effect. UDP replies are
  attributed to the fixed server represented by this adapter.

The helper was launched under the already-authorized ADB shell. It selected an
existing active cellular Internet network; no root, installed APK, firewall edits,
global route changes, Wi-Fi disconnect or persistent settings changes were needed.
Its framework bootstrap uses debug-only Android interfaces and is not a portable
production app implementation. A production Android component should use an
ordinary application context, request/retain the chosen cellular network, handle
network loss, and provide explicit endpoint/permission configuration.

## Scope and cleanup

This proves **relay-only control and data traffic** can use cellular while Wi-Fi
and wireless ADB stay connected. It does not yet provide automatic/direct-path
selection through the adapter, and it is not a throughput benchmark. Extra local
forwarding introduces processing overhead that must be measured before comparing
this setup with the earlier direct VM measurements.

The later [archived reverse direct video test](../archive/performance-2026-10-25/REVERSE_CELLULAR_DIRECT_VIDEO.md) extended
the UDP adapter with original IPv4/port metadata, using one cell-bound socket
and restricting destinations to the server and PC public address. Direct-only
file transfer was then verified with zero relayed messages. This extension does
not change the earlier relay proof; auto-mode transitions through the adapter
have not been tested.

The temporary bridge stops automatically after 120 seconds and was confirmed
stopped. The phone remains on Wi-Fi. Probe/bridge files are retained for repeatable
debugging; no background service was installed.

Ignored evidence and source snapshots are under
`artifacts/wifi-with-cellular-20261024-224904/`: network probe output, server TCP
peers, phone/PC logs, echo summary/CSV, the two Java helpers and the Python adapter.
Working helper sources and compiled artifacts are under `.local/android-cellular/`.
The phone adapter is `~/p2p-overlay-client/phone_via_cellular.py`; compiled helpers
are in `/data/local/tmp/overlay-cellular-{probe,bridge}.jar`.

## Wireless ADB discovery repair

The PC originally used Platform-Tools 34.0.5, whose mDNS discovery did not work
in this environment. Updating the existing Android SDK `platform-tools` folder
to the official 37.0.1 build restored discovery through `LIBADBMDNS` with
`mdns_enabled: true`. Pairing keys were preserved. After a fresh ADB server restart,
the paired Pixel 10 was automatically discovered and connected in about 1.3
seconds, without a pairing code or manual `adb connect`. A shell command and the
forwarded Debian SSH connection both succeeded. No firewall rules or router
configuration were changed. Backup tools, a hash manifest and verification output
are retained under ignored `.local/adb-mdns-update/`.

For future use, keep wireless debugging enabled on a reachable Wi-Fi network and
start the SDK's `adb` server. `adb mdns services` lists discovered devices and
`adb devices -l` shows the connected device. Restarting ADB clears port forwards;
restore the project's SSH forward with `adb forward tcp:2222 tcp:2222` when needed.

## Persistent Debian SSH listener

The Debian `ssh.service` is enabled at guest boot. Its configuration snippet
`/etc/ssh/sshd_config.d/20-overlay-ssh-ports.conf` makes the managed service listen
on both 22 and the Android-forwarded management port 2222. The earlier manually
launched `sshd -p 2222` listener was replaced by this service. Configuration
validation passed, and a fresh key-authenticated connection to 2222 succeeded
after restarting `ssh.service`. Future guest starts do not require manually
launching sshd. This does not make Android automatically boot the Debian VM.

Previous SSH configuration is backed up in the guest under
`/var/backups/overlay-ssh-20261024-210241/`; local application evidence is under
ignored `.local/phone/ssh-persistence-20261024-230239/`.

Android's official APIs describe
[binding individual sockets to a Network](https://developer.android.com/reference/android/net/Network#bindSocket(java.net.DatagramSocket))
and [requesting a network](https://developer.android.com/reference/android/net/ConnectivityManager#requestNetwork(android.net.NetworkRequest,android.net.ConnectivityManager.NetworkCallback)).
