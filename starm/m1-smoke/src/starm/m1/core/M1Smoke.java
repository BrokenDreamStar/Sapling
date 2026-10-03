package starm.m1.core;

import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/** Logs each verification marker exactly once, so hot-path probes stay cheap. */
public final class M1Smoke {
    private static final Set<String> SEEN = ConcurrentHashMap.newKeySet();

    public static void mark(String marker) {
        if (SEEN.add(marker)) {
            System.out.println("[ECO-M1] " + marker);
        }
    }

    private M1Smoke() {}
}
