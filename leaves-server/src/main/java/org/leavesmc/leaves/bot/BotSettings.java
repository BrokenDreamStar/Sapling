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

    /** Sapling extension (from Leaves): fetch and cache Mojang skin textures for fake players. */
    public static boolean useSkinCache() {
        return LeavesConfig.fakeplayer.useSkinCache;
    }

    /** Sapling extension (from Leaves): bots count as sleeping without a bed (fake sleeping). */
    public static boolean skipSleepCheck() {
        return LeavesConfig.fakeplayer.skipSleepCheck;
    }

    /** Sapling extension (from Leaves): fake players accumulate insomnia and spawn phantoms. */
    public static boolean spawnPhantom() {
        return LeavesConfig.fakeplayer.spawnPhantom;
    }
}
