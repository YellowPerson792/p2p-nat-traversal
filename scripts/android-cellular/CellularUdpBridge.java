import android.net.*;
import java.io.*;
import java.net.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;

/** Project UDP datagrams remain UDP over USB and cellular, never UDP-over-TCP. */
public class CellularUdpBridge {
    static volatile boolean running = true;
    static final Map<SocketAddress, Flow> flows = new ConcurrentHashMap<>();
    static final AtomicLong up = new AtomicLong(), down = new AtomicLong(), rejected = new AtomicLong();
    static InetAddress server, peer;
    static int udpPort, controlPort;
    static boolean allowed(InetAddress address, int port) {
        return (address.equals(server) && port == udpPort)
            || (peer != null && address.equals(peer));
    }

    static class Flow {
        final SocketAddress source;
        final DatagramSocket external;
        volatile long lastActive = System.nanoTime();
        Flow(SocketAddress source, Network cell, DatagramSocket local) throws IOException {
            this.source = source;
            external = new DatagramSocket(null);
            try {
                external.setReceiveBufferSize(2 * 1024 * 1024);
                external.setSendBufferSize(2 * 1024 * 1024);
                cell.bindSocket(external); external.bind(new InetSocketAddress(0));
            } catch (IOException e) { external.close(); throw e; }
            Thread receiver = new Thread(() -> {
                byte[] buffer = new byte[8202]; // Extra byte detects oversized/truncated datagrams.
                buffer[0] = 'O'; buffer[1] = 'C'; buffer[2] = '1';
                DatagramPacket packet = new DatagramPacket(buffer, 9, 8193);
                try {
                    while (running) {
                        packet.setLength(8193); external.receive(packet);
                        if (packet.getLength() > 8192 || !allowed(packet.getAddress(), packet.getPort())) {
                            rejected.incrementAndGet(); continue;
                        }
                        System.arraycopy(packet.getAddress().getAddress(), 0, buffer, 3, 4);
                        buffer[7] = (byte) (packet.getPort() >> 8); buffer[8] = (byte) packet.getPort();
                        local.send(new DatagramPacket(buffer, packet.getLength() + 9, source));
                        lastActive = System.nanoTime(); down.addAndGet(packet.getLength());
                    }
                } catch (IOException e) { if (running && !external.isClosed()) System.err.println(e); }
                finally { external.close(); flows.remove(source, this); }
            }, "cellular-udp-return");
            receiver.setDaemon(true); receiver.start();
        }
    }

    static void control(Socket client, Network cell) {
        Socket remote = new Socket(); CellularProxy.sockets.add(remote);
        try {
            cell.bindSocket(remote); remote.connect(new InetSocketAddress(server, controlPort), 10000);
            Future<?> outgoing = CellularProxy.workers.submit(() -> CellularProxy.copy(client, remote));
            CellularProxy.copy(remote, client); outgoing.get();
        } catch (Exception e) { System.err.println("CONTROL " + e.getClass().getSimpleName()); }
        finally { CellularProxy.close(client); CellularProxy.close(remote); }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 8) throw new IllegalArgumentException("seconds bindIPv4 clientIPv4 serverIPv4 udpPort controlPort peerIPv4-or-none stopFile");
        int seconds = Integer.parseInt(args[0]);
        if (seconds < 1 || seconds > 900) throw new IllegalArgumentException("Lifetime must be 1..900 seconds");
        InetAddress bind = InetAddress.getByName(args[1]), clientAddress = InetAddress.getByName(args[2]);
        server = InetAddress.getByName(args[3]); udpPort = Integer.parseInt(args[4]); controlPort = Integer.parseInt(args[5]);
        peer = args[6].equals("none") ? null : InetAddress.getByName(args[6]); File stop = new File(args[7]);
        if (!(bind instanceof Inet4Address) || !(clientAddress instanceof Inet4Address)
            || !(server instanceof Inet4Address) || (peer != null && !(peer instanceof Inet4Address)))
            throw new IllegalArgumentException("IPv4 only");
        if (udpPort < 1 || udpPort > 65535 || controlPort < 1 || controlPort > 65535) throw new IllegalArgumentException("Invalid port");
        ConnectivityManager manager = CellularProxy.connectivity(); Network cell = null;
        for (Network n : manager.getAllNetworks()) {
            NetworkCapabilities caps = manager.getNetworkCapabilities(n);
            if (caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)
                && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) cell = n;
        }
        if (cell == null) throw new IOException("No active cellular Internet network");
        final Network network = cell;
        DatagramSocket local = new DatagramSocket(null);
        ServerSocket tcp = new ServerSocket();
        try {
            local.setReceiveBufferSize(2 * 1024 * 1024); local.setSendBufferSize(2 * 1024 * 1024);
            local.bind(new InetSocketAddress(bind, 49170)); local.setSoTimeout(500);
            tcp.bind(new InetSocketAddress(bind, 49171)); tcp.setSoTimeout(500);
            Thread control = new Thread(() -> {
                while (running) try {
                    Socket socket = tcp.accept();
                    if (!socket.getInetAddress().equals(clientAddress)) { socket.close(); continue; }
                    CellularProxy.sockets.add(socket);
                    try { CellularProxy.workers.execute(() -> control(socket, network)); }
                    catch (RejectedExecutionException e) { CellularProxy.close(socket); }
                } catch (SocketTimeoutException ignored) {
                } catch (IOException e) { if (running) System.err.println(e); }
            });
            control.setDaemon(true); control.start();
            System.out.println("READY udp=" + bind.getHostAddress() + ":49170 tcp=49171 cellular=" + cell
                + " interface=" + manager.getLinkProperties(cell).getInterfaceName() + " default=" + manager.getActiveNetwork());
            System.out.flush();
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(seconds);
            byte[] buffer = new byte[8202]; DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
            while (System.nanoTime() < deadline && !stop.exists()) {
                // One cellular socket per PC UDP endpoint preserves independent NAT mappings.
                long now = System.nanoTime();
                for (Flow f : flows.values()) if (now - f.lastActive > TimeUnit.SECONDS.toNanos(60)) {
                    flows.remove(f.source, f); f.external.close();
                }
                try { packet.setLength(buffer.length); local.receive(packet); }
                catch (SocketTimeoutException e) { continue; }
                int n = packet.getLength(), port = ((buffer[7] & 255) << 8) | (buffer[8] & 255);
                if (!packet.getAddress().equals(clientAddress) || n < 9 || n > 8201
                    || buffer[0] != 'O' || buffer[1] != 'C' || buffer[2] != '1') { rejected.incrementAndGet(); continue; }
                InetAddress destination = InetAddress.getByAddress(Arrays.copyOfRange(buffer, 3, 7));
                if (!allowed(destination, port)) { rejected.incrementAndGet(); continue; }
                SocketAddress source = packet.getSocketAddress(); Flow flow = flows.get(source);
                try {
                    if (flow == null) {
                        if (flows.size() >= 16) { rejected.incrementAndGet(); continue; }
                        flow = new Flow(source, network, local); flows.put(source, flow);
                    }
                    flow.external.send(new DatagramPacket(buffer, 9, n - 9, destination, port));
                    flow.lastActive = System.nanoTime(); up.addAndGet(n - 9);
                } catch (IOException e) {
                    if (flow != null) { flows.remove(source, flow); flow.external.close(); }
                    System.err.println("UDP " + e.getClass().getSimpleName());
                }
            }
        } finally {
            running = false; local.close(); tcp.close();
            for (Flow f : flows.values()) f.external.close();
            for (Socket s : CellularProxy.sockets) CellularProxy.close(s);
            CellularProxy.workers.shutdownNow();
        }
        System.out.println("STOP udp_up_bytes=" + up.get() + " udp_down_bytes=" + down.get()
            + " rejected=" + rejected.get() + " default=" + manager.getActiveNetwork()); System.exit(0);
    }
}
