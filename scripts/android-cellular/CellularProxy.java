import android.content.*;
import android.net.*;
import android.os.Looper;
import java.io.*;
import java.net.*;
import java.util.Set;
import java.util.concurrent.*;

/** Debug-only SOCKS5 CONNECT proxy. Outgoing sockets and DNS use cellular only. */
public class CellularProxy {
    static final Set<Socket> sockets = ConcurrentHashMap.newKeySet();
    static final ExecutorService workers = new ThreadPoolExecutor(0, 64, 30,
        TimeUnit.SECONDS, new SynchronousQueue<Runnable>(), new ThreadPoolExecutor.AbortPolicy());

    static ConnectivityManager connectivity() throws Exception {
        Looper.prepareMainLooper();
        Class<?> tc = Class.forName("android.app.ActivityThread");
        Object at = tc.getMethod("systemMain").invoke(null);
        Context base = (Context) tc.getMethod("getSystemContext").invoke(at);
        Context context = new ContextWrapper(base.createPackageContext("com.android.shell", 0)) {
            @Override public String getPackageName() { return "com.android.shell"; }
            @Override public String getOpPackageName() { return "com.android.shell"; }
            @Override public Context getApplicationContext() { return this; }
            @Override public AttributionSource getAttributionSource() {
                return new AttributionSource.Builder(android.os.Process.myUid()).setPackageName("com.android.shell").build();
            }
        };
        Object binder = Class.forName("android.os.ServiceManager").getMethod("getService", String.class).invoke(null, "connectivity");
        Class<?> iface = Class.forName("android.net.IConnectivityManager");
        Object service = Class.forName("android.net.IConnectivityManager$Stub")
            .getMethod("asInterface", android.os.IBinder.class).invoke(null, binder);
        return (ConnectivityManager) ConnectivityManager.class.getConstructor(Context.class, iface).newInstance(context, service);
    }

    static void close(Socket s) { sockets.remove(s); try { s.close(); } catch (IOException ignored) {} }

    static void reply(OutputStream out, int status) throws IOException {
        out.write(new byte[] {5, (byte) status, 0, 1, 0, 0, 0, 0, 0, 0}); out.flush();
    }

    static void copy(Socket from, Socket to) {
        try {
            byte[] buffer = new byte[65536]; int n;
            InputStream in = from.getInputStream(); OutputStream out = to.getOutputStream();
            while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
            // EOF closes only this direction: preserve responses after an upload's FIN.
            to.shutdownOutput();
        } catch (IOException e) { close(from); close(to); }
    }

    static void serve(Socket client, Network network) {
        Socket remote = new Socket(); sockets.add(remote);
        boolean connected = false;
        try {
            client.setSoTimeout(10000);
            DataInputStream in = new DataInputStream(client.getInputStream());
            OutputStream out = client.getOutputStream();
            if (in.readUnsignedByte() != 5) return;
            int count = in.readUnsignedByte(); boolean noAuth = false;
            for (int i = 0; i < count; i++) if (in.readUnsignedByte() == 0) noAuth = true;
            out.write(new byte[] {5, (byte) (noAuth ? 0 : 255)}); out.flush();
            if (!noAuth) return;
            int version = in.readUnsignedByte(), command = in.readUnsignedByte(), reserved = in.readUnsignedByte();
            if (version != 5 || reserved != 0) { reply(out, 1); return; }
            if (command != 1) { reply(out, 7); return; } // UDP ASSOCIATE/BIND are explicitly unsupported.
            int kind = in.readUnsignedByte(); InetAddress address;
            if (kind == 1 || kind == 4) {
                byte[] raw = new byte[kind == 1 ? 4 : 16]; in.readFully(raw);
                address = InetAddress.getByAddress(raw);
            } else if (kind == 3) {
                byte[] raw = new byte[in.readUnsignedByte()]; in.readFully(raw);
                // Resolving through Network avoids accidentally using Wi-Fi DNS.
                address = network.getByName(new String(raw, java.nio.charset.StandardCharsets.US_ASCII));
            } else { reply(out, 8); return; }
            int port = in.readUnsignedShort();
            network.bindSocket(remote); // Bind BEFORE connect; never fall back to Wi-Fi.
            remote.connect(new InetSocketAddress(address, port), 10000);
            client.setSoTimeout(0); reply(out, 0); connected = true;
            Future<?> upstream = workers.submit(() -> copy(client, remote));
            copy(remote, client); upstream.get();
        } catch (Exception e) {
            if (!connected) try { reply(client.getOutputStream(), 1); } catch (IOException ignored) {}
            System.err.println("CONNECTION " + e.getClass().getSimpleName());
        } finally { close(client); close(remote); }
    }

    public static void main(String[] args) throws Exception {
        // Default listener is accessible only through adb forward. For direct USB,
        // supply the phone USB address plus the single permitted PC source address.
        int lifetime = args.length > 0 ? Integer.parseInt(args[0]) : 600;
        if (lifetime < 1 || lifetime > 900) throw new IllegalArgumentException("Lifetime must be 1..900 seconds");
        String bind = args.length > 1 ? args[1] : "127.0.0.1";
        String allowed = args.length > 2 ? args[2] : "127.0.0.1";
        File stop = args.length > 3 ? new File(args[3]) : null;
        ConnectivityManager manager = connectivity(); Network cell = null;
        for (Network n : manager.getAllNetworks()) {
            NetworkCapabilities c = manager.getNetworkCapabilities(n);
            if (c != null && c.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)
                && c.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) cell = n;
        }
        if (cell == null) throw new IOException("No active cellular Internet network");
        final Network network = cell; final InetAddress permitted = InetAddress.getByName(allowed);
        ServerSocket listener = new ServerSocket(); listener.bind(new InetSocketAddress(bind, 49172));
        listener.setSoTimeout(500);
        System.out.println("READY socks5=" + bind + ":49172 allowed=" + allowed + " cellular=" + cell
            + " interface=" + manager.getLinkProperties(cell).getInterfaceName() + " default=" + manager.getActiveNetwork());
        System.out.flush(); long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(lifetime);
        try {
            while (System.nanoTime() < deadline && (stop == null || !stop.exists())) {
                Socket client;
                try { client = listener.accept(); } catch (SocketTimeoutException e) { continue; }
                if (!client.getInetAddress().equals(permitted)) { client.close(); continue; }
                sockets.add(client);
                try { workers.execute(() -> serve(client, network)); }
                catch (RejectedExecutionException e) { close(client); }
            }
        } finally {
            listener.close(); for (Socket s : sockets) close(s); workers.shutdownNow();
        }
        System.out.println("STOP default=" + manager.getActiveNetwork()); System.exit(0);
    }
}
