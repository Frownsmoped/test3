import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Minimal entrypoint for native-image AOT compilation.
 *
 * Behavior:
 * 1) Ensures eula.txt exists with "eula=true".
 * 2) Delegates to the real Minecraft server main, passing through args.
 *
 * This is useful when you want a controlled entrypoint that always generates EULA.
 */
public final class SelfMain {
    private SelfMain() {}

    public static void main(String[] args) {
        try {
            // Match Minecraft server behavior: EULA is checked in current working directory.
            // Avoid repeated disk writes: only create it if missing.
            Path eula = Path.of("eula.txt");
            if (!Files.exists(eula)) {
                Files.writeString(eula, "eula=true\n", StandardCharsets.UTF_8);
            }
        } catch (IOException e) {
            // Don't fail hard; still try to start the server. Print for debugging.
            e.printStackTrace();
        }

        // Pre-bind 25565; if occupied, increment until a free port is found.
        // Then pass --port <freePort> to the server.
        int port = 25565;
        while (true) {
            try (ServerSocket ss = new ServerSocket()) {
                ss.setReuseAddress(false);
                ss.bind(new InetSocketAddress("0.0.0.0", port));
                break;
            } catch (IOException ignored) {
                port++;
                // Avoid infinite loop in pathological cases.
                if (port > 65535) {
                    throw new RuntimeException("No available port found in range 25565-65535");
                }
            }
        }

        // Delegate to the real server main.
        // Force unsupported terminal mode in native-image builds to avoid JLine/Jansi native console init.
        System.setProperty("jline.terminal", "jline.UnsupportedTerminal");

        // Force --nogui / --nojline / --noconsole and inject --port while preserving user args.
        String[] forwarded;
        if (args == null || args.length == 0) {
            forwarded = new String[] {
                "--nogui",
                "--nojline",
                "--noconsole",
                "--port",
                String.valueOf(port)
            };
        } else {
            forwarded = new String[args.length + 5];
            forwarded[0] = "--nogui";
            forwarded[1] = "--nojline";
            forwarded[2] = "--noconsole";
            forwarded[3] = "--port";
            forwarded[4] = String.valueOf(port);
            System.arraycopy(args, 0, forwarded, 5, args.length);
        }

        // Call server main via reflection; class name can be overridden by env var.
        // Default: org.bukkit.craftbukkit.Main
        String entryClass = System.getenv().getOrDefault("MC_ENTRY_CLASS", "org.bukkit.craftbukkit.Main");
        try {
            Class<?> mainClz = Class.forName(entryClass);
            java.lang.reflect.Method m = mainClz.getMethod("main", String[].class);
            m.invoke(null, (Object) forwarded);
        } catch (Throwable t) {
            t.printStackTrace();
        }
    }
}
