package starm.m1.core.mixin;

import com.llamalad7.mixinextras.injector.ModifyReturnValue;
import starm.m1.core.M1Smoke;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * M1 smoke test v2: proves (a) plain Mixin applies to server classes and (b) MixinExtras
 * injectors are processed. Targets verified via javap against the 26.1.2 runtime jar:
 * MinecraftServer#runServer()V and #getTickCount()I.
 */
@Mixin(targets = "net.minecraft.server.MinecraftServer")
public class M1SmokeMixin {
    @Inject(method = "runServer()V", at = @At("HEAD"))
    private void eco$plainMixinProbe(CallbackInfo ci) {
        M1Smoke.mark("plain-mixin applied");
    }

    @ModifyReturnValue(method = "getTickCount()I", at = @At("RETURN"))
    private int eco$mixinExtrasProbe(int original) {
        M1Smoke.mark("mixinextras applied");
        return original;
    }
}
