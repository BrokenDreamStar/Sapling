package org.leavesmc.leaves.bot;

import org.leavesmc.leaves.LeavesConfig;

/**
 * Sapling port of the Carpet settings that affect fake players.
 * Values are read from leaves.yml on every access, matching Carpet's dynamic setting behaviour.
 */
public final class BotSettings {
    private BotSettings() {
    }

    /** Carpet: CarpetSettings.commandPlayer, default "ops". */
    public static String commandPlayer() {
        return LeavesConfig.fakeplayer.commandPlayer;
    }

    /** Carpet: CarpetSettings.allowSpawningOfflinePlayers, default true. */
    public static boolean allowSpawningOfflinePlayers() {
        return LeavesConfig.fakeplayer.allowSpawningOfflinePlayers;
    }

    /** Carpet: CarpetSettings.allowListingFakePlayers, default false. */
    public static boolean allowListingFakePlayers() {
        return LeavesConfig.fakeplayer.allowListingFakePlayers;
    }
}
