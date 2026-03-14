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

            Path eula = Path.of("eula.txt");
            if (!Files.exists(eula)) {
                Files.writeString(eula, "eula=true\n", StandardCharsets.UTF_8);
            }
        } catch (IOException e) {
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
        String[] forwarded;
        if (args == null || args.length == 0) {
            forwarded = new String[] { "--nogui", "--port", String.valueOf(port) };
        } else {
            forwarded = new String[args.length + 3];
            forwarded[0] = "--nogui";
            forwarded[1] = "--port";
            forwarded[2] = String.valueOf(port);
            System.arraycopy(args, 0, forwarded, 3, args.length);
        }

        // For Spigot/Paper materialized jar we use org.bukkit.craftbukkit.Main.
        org.bukkit.craftbukkit.Main.main(forwarded);
    }
}
