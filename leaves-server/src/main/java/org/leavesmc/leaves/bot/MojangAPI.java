package org.leavesmc.leaves.bot;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.URLConnection;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

/**
 * Mojang skin texture lookup for fake players (restored Leaves feature).
 * <p>
 * Only used when {@code settings.fakeplayer.cache-skin} is enabled. Results are
 * cached in memory (including failures) so respawning a fake player with the
 * same name does not hit the Mojang API again.
 */
public final class MojangAPI {

    private static final Map<String, String[]> CACHE = new HashMap<>();

    private MojangAPI() {
    }

    /** Returns {textures value, signature} or null when the lookup failed. */
    public static synchronized String[] getSkin(String name) {
        if (BotSettings.useSkinCache() && CACHE.containsKey(name)) {
            return CACHE.get(name);
        }

        String[] values = pullFromAPI(name);
        CACHE.put(name, values);
        return values;
    }

    public static CompletableFuture<String[]> getSkinAsync(String name) {
        return CompletableFuture.supplyAsync(() -> getSkin(name));
    }

    private static String[] pullFromAPI(String name) {
        try {
            JsonObject profile = readJson("https://api.mojang.com/users/profiles/minecraft/" + name);
            if (profile == null || !profile.has("id")) {
                return null;
            }
            String uuid = profile.get("id").getAsString();
            JsonObject session = readJson("https://sessionserver.mojang.com/session/minecraft/profile/" + uuid + "?unsigned=false");
            if (session == null || !session.has("properties")) {
                return null;
            }
            JsonObject property = session.getAsJsonArray("properties").get(0).getAsJsonObject();
            return new String[]{property.get("value").getAsString(), property.get("signature").getAsString()};
        } catch (IOException | IllegalStateException | IllegalArgumentException | IndexOutOfBoundsException e) {
            return null;
        }
    }

    private static JsonObject readJson(String url) throws IOException {
        URLConnection connection = URI.create(url).toURL().openConnection();
        connection.setConnectTimeout(5000);
        connection.setReadTimeout(10000);
        try (InputStreamReader reader = new InputStreamReader(connection.getInputStream())) {
            return JsonParser.parseReader(reader).getAsJsonObject();
        }
    }
}
