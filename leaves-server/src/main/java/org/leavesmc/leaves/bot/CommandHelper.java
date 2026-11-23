// Ported from fabric-carpet @9c1bec181eebae1cd8fbab3e1872bb6cd8139071 (MIT)
package org.leavesmc.leaves.bot;

import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;

public final class CommandHelper {
    private CommandHelper() {
    }

    /**
     * Whether the given source has enough permission level to run a command that requires the given commandLevel.
     * Identical to Carpet's CommandHelper.canUseCommand.
     */
    public static boolean canUseCommand(CommandSourceStack source, Object commandLevel) {
        if (commandLevel instanceof Boolean b) return b;
        String commandLevelString = commandLevel.toString();
        return switch (commandLevelString) {
            case "true" -> true;
            case "false" -> false;
            case "ops" -> Commands.LEVEL_GAMEMASTERS.check(source.permissions());
            case "0" -> Commands.LEVEL_ALL.check(source.permissions());
            case "1" -> Commands.LEVEL_MODERATORS.check(source.permissions());
            case "2" -> Commands.LEVEL_GAMEMASTERS.check(source.permissions());
            case "3" -> Commands.LEVEL_ADMINS.check(source.permissions());
            case "4" -> Commands.LEVEL_OWNERS.check(source.permissions());
            default -> false;
        };
    }
}
