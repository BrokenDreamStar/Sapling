package org.leavesmc.leaves.bot;

import net.minecraft.commands.CommandSourceStack;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import org.bukkit.Bukkit;
import org.bukkit.plugin.ServicesManager;
import org.leavesmc.leaves.LeavesLogger;

import java.lang.reflect.Method;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

/**
 * Sapling extension (restored Leaves bot limit + LuckPerms integration).
 * <p>
 * The server-wide limit counts every {@link FakePlayer}. The per-player limit is
 * resolved from the LuckPerms meta value {@code settings.fakeplayer.luckperms-meta-key}
 * (inherited from the player's groups as usual for LuckPerms meta), falling back to
 * {@code settings.fakeplayer.player-limit} when LuckPerms or the meta value is absent.
 */
public final class BotLimits {

    private static boolean luckPermsInitialised;
    private static Object luckPerms;
    private static Method getUserManager;
    private static Method getUser;

    private BotLimits() {
    }

    public static int currentBots(MinecraftServer server) {
        int count = 0;
        for (ServerPlayer player : server.getPlayerList().getPlayers()) {
            if (player instanceof FakePlayer) {
                count++;
            }
        }
        return count;
    }

    public static int botsCreatedBy(MinecraftServer server, UUID creator) {
        int count = 0;
        for (ServerPlayer player : server.getPlayerList().getPlayers()) {
            if (player instanceof FakePlayer fake && creator.equals(fake.creatorUuid)) {
                count++;
            }
        }
        return count;
    }

    /**
     * Checks the server-wide limit (and, unless shadowing, the executor's personal limit).
     * Sends the refusal message and returns false when creation must be denied.
     */
    public static boolean canCreate(CommandSourceStack source, boolean shadow) {
        MinecraftServer server = source.getServer();
        int globalLimit = BotSettings.limit();
        if (globalLimit > 0 && currentBots(server) >= globalLimit) {
            Messenger.m(source, "r Bot number limit exceeded");
            return false;
        }
        if (!shadow && source.getEntity() instanceof ServerPlayer creator) {
            int playerLimit = limitForPlayer(creator);
            if (playerLimit >= 0 && botsCreatedBy(server, creator.getUUID()) >= playerLimit) {
                Messenger.m(source, "r You have reached your bot limit (", "rb " + playerLimit, "r )");
                return false;
            }
        }
        return true;
    }

    private static int limitForPlayer(ServerPlayer player) {
        String metaValue = luckPermsMeta(player.getUUID(), BotSettings.luckPermsMetaKey());
        if (metaValue != null) {
            try {
                return Integer.parseInt(metaValue.trim());
            } catch (NumberFormatException ignored) {
                // fall back to the config default for malformed meta values
            }
        }
        return BotSettings.playerLimit();
    }

    private static String luckPermsMeta(UUID uuid, String metaKey) {
        try {
            if (!initLuckPerms()) {
                return null;
            }
            Object userManager = getUserManager.invoke(luckPerms);
            Object future = getUser.invoke(userManager, uuid);
            if (!(future instanceof CompletableFuture<?> completableFuture)) {
                return null;
            }
            Object user = completableFuture.getNow(null); // do not block the server thread
            if (user == null) {
                return null;
            }
            Object cachedData = user.getClass().getMethod("getCachedData").invoke(user);
            Object metaData = cachedData.getClass().getMethod("getMetaData").invoke(cachedData);
            Object value = metaData.getClass().getMethod("getMetaValue", String.class).invoke(metaData, metaKey);
            return value instanceof String string ? string : null;
        } catch (Throwable throwable) {
            // LuckPerms is optional; never let a lookup failure break /player
            if (!luckPermsInitialised) {
                LeavesLogger.LOGGER.warn("Unable to query LuckPerms for {}", uuid, throwable);
            }
            return null;
        }
    }

    private static synchronized boolean initLuckPerms() {
        if (luckPermsInitialised) {
            return luckPerms != null;
        }
        luckPermsInitialised = true;
        try {
            // LuckPerms classes live in the plugin class loader, so the service has to be
            // matched by name instead of Class.forName (which only sees the server loader).
            ServicesManager services = Bukkit.getServicesManager();
            for (Class<?> service : services.getKnownServices()) {
                if (!"net.luckperms.api.LuckPerms".equals(service.getName())) {
                    continue;
                }
                Object provider = services.load((Class) service);
                if (provider == null) {
                    continue;
                }
                Method userManagerMethod = provider.getClass().getMethod("getUserManager");
                Object userManager = userManagerMethod.invoke(provider);
                if (userManager == null) {
                    continue;
                }
                Method getUserMethod = userManager.getClass().getMethod("getUser", UUID.class);
                luckPerms = provider;
                getUserManager = userManagerMethod;
                getUser = getUserMethod;
                LeavesLogger.LOGGER.info("Using LuckPerms for per-player bot limits (meta key: {})", BotSettings.luckPermsMetaKey());
                break;
            }
        } catch (Throwable throwable) {
            luckPerms = null;
            LeavesLogger.LOGGER.warn("Unable to hook into LuckPerms; falling back to settings.fakeplayer.player-limit", throwable);
        }
        return luckPerms != null;
    }
}
