package org.leavesmc.leaves.replay;

import com.google.gson.JsonElement;
import com.mojang.datafixers.DataFixer;
import net.minecraft.server.MinecraftServer;
import net.minecraft.stats.ServerStatsCounter;
import net.minecraft.stats.Stat;
import net.minecraft.world.entity.player.Player;
import org.jetbrains.annotations.NotNull;

import java.io.File;

public class PhotographerStatsCounter extends ServerStatsCounter {

    private static final File UNKNOWN_FILE = new File("PHOTOGRAPHER_STATS_REMOVE_THIS");

    public PhotographerStatsCounter(MinecraftServer server) {
        super(server, UNKNOWN_FILE.toPath());
    }

    @Override
    public void save() {
    }

    @Override
    public void setValue(@NotNull Player player, @NotNull Stat<?> stat, int value) {
    }

    @Override
    public void parse(DataFixer fixerUpper, JsonElement json) {
    }

    @Override
    public int getValue(@NotNull Stat<?> stat) {
        return 0;
    }
}
