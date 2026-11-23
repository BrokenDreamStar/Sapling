// Ported from fabric-carpet @9c1bec181eebae1cd8fbab3e1872bb6cd8139071 (MIT)
// https://github.com/gnembon/fabric-carpet (branch 26.1)
package org.leavesmc.leaves.bot;

import net.minecraft.network.Connection;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.contents.TranslatableContents;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.CommonListenerCookie;
import net.minecraft.server.network.ServerGamePacketListenerImpl;
import net.minecraft.world.entity.PositionMoveRotation;
import net.minecraft.world.entity.Relative;
import java.util.Set;

public class FakePacketListener extends ServerGamePacketListenerImpl
{
    public FakePacketListener(final MinecraftServer minecraftServer, final Connection connection, final ServerPlayer serverPlayer, final CommonListenerCookie i)
    {
        super(minecraftServer, connection, serverPlayer, i);
    }

    @Override
    public void disconnect(Component message)
    {
        if (message.getContents() instanceof TranslatableContents text && (text.getKey().equals("multiplayer.disconnect.idling") || text.getKey().equals("multiplayer.disconnect.duplicate_login")))
        {
            ((FakePlayer) player).kill(message);
        }
    }

    // Paper's ServerPlayer.isInvulnerableTo() treats a player whose client has not
    // loaded yet as invulnerable. A fake player has no client, and Carpet's fake
    // players are damageable immediately, so report the client as loaded right away.
    @Override
    public boolean hasClientLoaded() {
        return true;
    }

    @Override
    public void tickClientLoadTimeout() {
        // no client to wait for
    }

    @Override
    public void tick() {
        // fake connections are not part of the network tick loop; keep-alive and
        // timeout handling do not apply
    }

    @Override
    public boolean isAcceptingMessages() {
        return true;
    }

    @Override
    public void teleport(PositionMoveRotation positionMoveRotation, Set<Relative> set)
    {
        super.teleport(positionMoveRotation, set);
        if (player.level().getPlayerByUUID(player.getUUID()) != null) {
            resetPosition();
            player.level().getChunkSource().move(player);
        }
    }

}



