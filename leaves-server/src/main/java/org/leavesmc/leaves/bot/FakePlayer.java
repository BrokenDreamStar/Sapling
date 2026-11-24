// Ported from fabric-carpet @9c1bec181eebae1cd8fbab3e1872bb6cd8139071 (MIT)
// https://github.com/gnembon/fabric-carpet (branch 26.1)
package org.leavesmc.leaves.bot;

import com.google.common.collect.ImmutableMultimap;
import com.mojang.authlib.GameProfile;
import com.mojang.authlib.properties.Property;
import com.mojang.authlib.properties.PropertyMap;
import net.minecraft.core.UUIDUtil;
import net.minecraft.network.DisconnectionDetails;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.contents.TranslatableContents;
import net.minecraft.network.protocol.PacketFlow;
import net.minecraft.network.protocol.game.ClientboundEntityPositionSyncPacket;
import net.minecraft.network.protocol.game.ClientboundPlayerInfoUpdatePacket;
import net.minecraft.network.protocol.game.ClientboundRotateHeadPacket;
import org.leavesmc.leaves.LeavesLogger;
import net.minecraft.network.protocol.game.ServerboundClientCommandPacket;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.TickTask;
import net.minecraft.server.level.ClientInformation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.CommonListenerCookie;
import net.minecraft.server.players.OldUsersConverter;
import net.minecraft.util.ProblemReporter;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.entity.vehicle.boat.AbstractBoat;
import net.minecraft.world.food.FoodData;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.ResolvableProfile;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.portal.TeleportTransition;
import net.minecraft.world.level.storage.TagValueInput;
import net.minecraft.world.level.storage.ValueInput;
import net.minecraft.world.phys.Vec3;

import java.util.HashSet;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

@SuppressWarnings("EntityConstructor")
public class FakePlayer extends ServerPlayer
{
    private static final Set<String> spawning = new HashSet<>();

    public Runnable fixStartingPosition = () -> {};
    public boolean isAShadow;
    public int notSleepTicks; // Sapling - fake player insomnia counter (Leaves spawn-phantom)

    // Returns true if it was successful, false if couldn't spawn due to the player not existing in Mojang servers
    public static boolean createFake(String username, MinecraftServer server, Vec3 pos, double yaw, double pitch, ResourceKey<Level> dimensionId, GameType gamemode, boolean flying)
    {
        //prolly half of that crap is not necessary, but it works
        ServerLevel worldIn = server.getLevel(dimensionId);
        server.services().nameToIdCache().resolveOfflineUsers(false);
        GameProfile gameprofile;

            UUID uuid = OldUsersConverter.convertMobOwnerIfNecessary(server, username);
            //NameAndId res = server.services().nameToIdCache().get(username).orElseThrow(); //findByName  .orElse(null)
            if (uuid == null && BotSettings.allowSpawningOfflinePlayers()) {
                server.services().nameToIdCache().resolveOfflineUsers(server.isDedicatedServer() && server.usesAuthentication());
                uuid = UUIDUtil.createOfflinePlayerUUID(username);
            }
            if (uuid == null) {
                return false; // no uuid, no player
            }
            gameprofile = new GameProfile(uuid, username);


        //GameProfile finalGP = gameprofile;

        // We need to mark this player as spawning so that we do not
        // try to spawn another player with the name while the profile
        // is being fetched - preventing multiple players spawning
        String name = gameprofile.name();
        spawning.add(name);

        CompletableFuture<ProfileAndSkin> profileFuture = fetchGameProfile(server, gameprofile.id())
            .thenApply(profile -> new ProfileAndSkin(profile, null));
        if (BotSettings.useSkinCache())
        {
            // Sapling - restored Leaves skin cache: resolve Mojang textures async and attach them
            profileFuture = profileFuture.thenCombine(MojangAPI.getSkinAsync(username),
                (profileAndSkin, skin) -> new ProfileAndSkin(profileAndSkin.profile(), skin));
        }
        profileFuture.whenCompleteAsync((profileAndSkin, t) -> {
            // Always remove the name, even if exception occurs
            spawning.remove(name);
            if (t != null)
            {
                return;
            }

            GameProfile current;
            if (profileAndSkin.profile().name().isEmpty()) {
                current = gameprofile;
            }
            else {
                current = profileAndSkin.profile();
            }
            // Sapling - apply the cached skin to whichever profile is used (offline resolution can be empty)
            if (profileAndSkin.skin() != null) {
                current = withSkin(current, profileAndSkin.skin());
            }

            FakePlayer instance = new FakePlayer(server, worldIn, current, ClientInformation.createDefault(), false);
            instance.fixStartingPosition = () -> instance.snapTo(pos.x, pos.y, pos.z, (float) yaw, (float) pitch);
            server.getPlayerList().placeNewPlayer(new FakeClientConnection(PacketFlow.SERVERBOUND), instance, new CommonListenerCookie(current, 0, instance.clientInformation(), false, null, new java.util.HashSet<>(), new io.papermc.paper.util.KeepAlive()));
            loadPlayerData(instance);
            instance.stopRiding(); // otherwise the created fake player will be on the vehicle
            instance.teleportTo(worldIn, pos.x, pos.y, pos.z, Set.of(), (float) yaw, (float) pitch, true);
            instance.setHealth(20.0F);
            instance.unsetRemoved();
            instance.getAttribute(Attributes.STEP_HEIGHT).setBaseValue(0.6F);
            instance.gameMode.changeGameModeForPlayer(gamemode);
            server.getPlayerList().broadcastAll(new ClientboundRotateHeadPacket(instance, (byte) (instance.yHeadRot * 256 / 360)), dimensionId);//instance.dimension);
            server.getPlayerList().broadcastAll(ClientboundEntityPositionSyncPacket.of(instance), dimensionId);//instance.dimension);
            //instance.world.getChunkManager(). updatePosition(instance);
            instance.entityData.set(DATA_PLAYER_MODE_CUSTOMISATION, (byte) 0x7f); // show all model layers (incl. capes)
            instance.getAbilities().flying = flying;
        }, server);
        return true;
    }

    private record ProfileAndSkin(GameProfile profile, String[] skin) {
    }

    /** Sapling - attach the cached Mojang textures property to an immutable authlib GameProfile. */
    private static GameProfile withSkin(GameProfile profile, String[] skin) {
        if (skin == null || skin.length < 2) {
            return profile;
        }
        ImmutableMultimap.Builder<String, Property> builder = ImmutableMultimap.builder();
        builder.putAll(profile.properties());
        builder.put("textures", new Property("textures", skin[0], skin[1]));
        GameProfile profileWithSkin = new GameProfile(profile.id(), profile.name(), new PropertyMap(builder.build()));
        return profileWithSkin;
    }

    private static CompletableFuture<GameProfile> fetchGameProfile(MinecraftServer server, final UUID name) {
        final ResolvableProfile resolvableProfile = ResolvableProfile.createUnresolved(name);
        return resolvableProfile.resolveProfile(server.services().profileResolver());
    }

    private static void loadPlayerData(FakePlayer player)
    {
        try (ProblemReporter.ScopedCollector scopedCollector = new ProblemReporter.ScopedCollector(player.problemPath(), LeavesLogger.LOGGER))
        {
            Optional<ValueInput> optional = player.level().getServer().getPlayerList().loadPlayerData(player.nameAndId()).map((compoundTag) -> TagValueInput.create(scopedCollector, player.registryAccess(), compoundTag));
            optional.ifPresent( valueInput -> {
                player.load(valueInput);
                player.loadAndSpawnEnderPearls(valueInput);
                player.loadAndSpawnParentVehicle(valueInput);
            });
        }
    }

    public static FakePlayer createShadow(MinecraftServer server, ServerPlayer player)
    {
        player.connection.disconnect(Component.translatable("multiplayer.disconnect.duplicate_login"));
        ServerLevel worldIn = player.level();//.getWorld(player.dimension);
        GameProfile gameprofile = player.getGameProfile();
        FakePlayer playerShadow = new FakePlayer(server, worldIn, gameprofile, player.clientInformation(), true);
        playerShadow.setChatSession(player.getChatSession());
        server.getPlayerList().placeNewPlayer(new FakeClientConnection(PacketFlow.SERVERBOUND), playerShadow, new CommonListenerCookie(gameprofile, 0, player.clientInformation(), true, null, new java.util.HashSet<>(), new io.papermc.paper.util.KeepAlive()));
        loadPlayerData(playerShadow);

        playerShadow.setHealth(player.getHealth());
        playerShadow.connection.teleport(player.getX(), player.getY(), player.getZ(), player.getYRot(), player.getXRot());
        playerShadow.gameMode.changeGameModeForPlayer(player.gameMode.getGameModeForPlayer());
        playerShadow.actionPack.copyFrom(player.actionPack);
        // this might create problems if a player logs back in...
        playerShadow.getAttribute(Attributes.STEP_HEIGHT).setBaseValue(0.6F);
        playerShadow.entityData.set(DATA_PLAYER_MODE_CUSTOMISATION, player.getEntityData().get(DATA_PLAYER_MODE_CUSTOMISATION));


        server.getPlayerList().broadcastAll(new ClientboundRotateHeadPacket(playerShadow, (byte) (player.yHeadRot * 256 / 360)), playerShadow.level().dimension());
        server.getPlayerList().broadcastAll(new ClientboundPlayerInfoUpdatePacket(ClientboundPlayerInfoUpdatePacket.Action.ADD_PLAYER, playerShadow));
        //player.world.getChunkManager().updatePosition(playerShadow);
        playerShadow.getAbilities().flying = player.getAbilities().flying;
        return playerShadow;
    }

    public static FakePlayer respawnFake(MinecraftServer server, ServerLevel level, GameProfile profile, ClientInformation cli)
    {
        return new FakePlayer(server, level, profile, cli, false);
    }

    public static boolean isSpawningPlayer(String username)
    {
        return spawning.contains(username);
    }

    private FakePlayer(MinecraftServer server, ServerLevel worldIn, GameProfile profile, ClientInformation cli, boolean shadow)
    {
        super(server, worldIn, profile, cli);
        this.isAShadow = shadow;
        // Sapling - restored Leaves skip-sleep-check: fake players count as sleeping without a bed
        this.fauxSleeping = BotSettings.skipSleepCheck();
    }

    @Override
    public void onEquipItem(final EquipmentSlot slot, final ItemStack previous, final ItemStack stack)
    {
        if (!isUsingItem()) super.onEquipItem(slot, previous, stack);
    }

    @Override
    public void kill(ServerLevel level)
    {
        kill(Messenger.s("Killed"));
    }

    public void kill(Component reason)
    {
        shakeOff();

        if (reason.getContents() instanceof TranslatableContents text && text.getKey().equals("multiplayer.disconnect.duplicate_login")) {
            this.connection.onDisconnect(new DisconnectionDetails(reason));
        } else {
            this.level().getServer().schedule(new TickTask(this.level().getServer().getTickCount(), () -> {
                this.connection.onDisconnect(new DisconnectionDetails(reason));
            }));
        }
    }

    @Override
    public void tick()
    {
        // Sapling - restored Leaves spawn-phantom: accumulate insomnia while not resting
        if (BotSettings.spawnPhantom())
        {
            this.notSleepTicks++;
        }
        if (this.isSleeping() && this.sleepCounter > 100)
        {
            this.notSleepTicks = 0;
        }
        if (this.level().getServer().getTickCount() % 10 == 0)
        {
            this.connection.resetPosition();
            this.level().getChunkSource().move(this);
        }
        try
        {
            super.tick();
            this.doTick();
        }
        catch (NullPointerException ignored)
        {
            // happens with that paper port thingy - not sure what that would fix, but hey
            // the game not gonna crash violently.
        }
    }

    @Override
    public boolean startRiding(Entity entityToRide, boolean force, boolean sendEventAndTriggers) {
        if (super.startRiding(entityToRide, force, sendEventAndTriggers)) {
            // from ClientPacketListener.handleSetEntityPassengersPacket
            if (entityToRide instanceof AbstractBoat) {
                this.yRotO = entityToRide.getYRot();
                this.setYRot(entityToRide.getYRot());
                this.setYHeadRot(entityToRide.getYRot());
            }
            return true;
        } else {
            return false;
        }
    }

    private void shakeOff()
    {
        if (getVehicle() instanceof Player) stopRiding();
        for (Entity passenger : getIndirectPassengers())
        {
            if (passenger instanceof Player) passenger.stopRiding();
        }
    }

    @Override
    public void die(DamageSource cause)
    {
        shakeOff();
        super.die(cause);
        setHealth(20);
        this.foodData = new FoodData();
        kill(this.getCombatTracker().getDeathMessage());
    }

    @Override
    public String getIpAddress()
    {
        return "127.0.0.1";
    }

    @Override
    public boolean allowsListing() {
        return BotSettings.allowListingFakePlayers();
    }

    @Override
    public ServerPlayer teleport(TeleportTransition serverLevel)
    {
        super.teleport(serverLevel);
        if (wonGame) {
            ServerboundClientCommandPacket p = new ServerboundClientCommandPacket(ServerboundClientCommandPacket.Action.PERFORM_RESPAWN);
            connection.handleClientCommand(p);
        }

        // If above branch was taken, *this* has been removed and replaced, the new instance has been set
        // on 'our' connection (which is now theirs, but we still have a ref).
        if (connection.player.isChangingDimension()) {
            connection.player.hasChangedDimension();
        }
        return connection.player;
    }
}
