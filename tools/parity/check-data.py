#!/usr/bin/env python3
"""Compare on-disk player side effects for fake players.

After a scenario the game writes world/players/{stats,advancements}/<uuid>.json
on disconnect. Ref and Sapling derive different offline UUIDs for the same name
(vanilla vs CraftBukkit offline-uuid algorithms), so the files are located by
glob, and platform-specific noise is normalised:

- stats: only deterministic sections are compared (mined/used/broken/crafted/
  picked_up/dropped/killed/killed_by); time counters differ by spawn wall-clock.
- advancements: timestamps are masked and the top-level DataVersion removed
  (Paper's data version is newer than Fabric's).
"""

import glob
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "parity"))
from parity import Rcon  # noqa: E402

SERVERS = {
    "ref": ("run-carpet.sh", 25575, "carpet-server"),
    "sapling": ("run-sapling.sh", 25576, "sapling-server"),
}
STATS_SECTIONS = (
    "minecraft:mined",
    "minecraft:used",
    "minecraft:broken",
    "minecraft:crafted",
    "minecraft:picked_up",
    "minecraft:dropped",
    "minecraft:killed",
    "minecraft:killed_by",
)
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}")


def run(script, *args):
    subprocess.run(
        [os.path.join(ROOT, "tools", "parity", script), *args],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def scenario(script, rcon_port):
    run(script, "--stop")
    run(script, "--reset-world", "--wait-ready")
    with Rcon("127.0.0.1", rcon_port, "parity") as rcon:
        rcon.command("/tick unfreeze")
        rcon.command("/forceload add 0 0")
        rcon.command("/gamerule advance_time false")
        rcon.command("/gamerule spawn_mobs false")
        rcon.command("/player DataBot kill")
        time.sleep(0.5)
        rcon.command("/player DataBot spawn in survival")
        time.sleep(3)
        rcon.command("/clear DataBot")
        rcon.command("/tp DataBot 0.5 -60 0.5 0 0")
        time.sleep(0.3)
        rcon.command("/setblock 1 -60 0 minecraft:dirt")
        rcon.command("/player DataBot look at 1.5 -59.5 0.5")
        time.sleep(0.2)
        rcon.command("/player DataBot attack continuous")
        time.sleep(1.5)
        rcon.command("/player DataBot stop")
        assert "Test passed" in rcon.command("/execute if block 1 -60 0 minecraft:air")
        rcon.command("/tp DataBot 1.5 -60 0.5")
        time.sleep(0.5)
        rcon.command("/give DataBot minecraft:stone 1")
        time.sleep(0.3)
        rcon.command("/item replace entity DataBot hotbar.1 with minecraft:snowball 1")
        rcon.command("/player DataBot hotbar 2")
        rcon.command("/player DataBot look up")
        rcon.command("/player DataBot use")
        time.sleep(0.3)
    run(script, "--stop")


def load_json(server_dir, kind):
    files = glob.glob(
        os.path.join(ROOT, ".carpet-ref", "parity", server_dir, "world", "players", kind, "*.json")
    )
    if not files:
        return None
    with open(files[0], "r", encoding="utf-8") as handle:
        return json.load(handle)


def normalise_stats(data):
    stats = data.get("stats", {})
    return {section: stats.get(section, {}) for section in STATS_SECTIONS}


def normalise_advancements(data):
    out = {}
    for key, value in data.items():
        if key == "DataVersion":
            continue
        out[key] = json.loads(TIMESTAMP_RE.sub("<ts>", json.dumps(value, sort_keys=True)))
    return out


def main():
    result = {}
    for label, (script, rcon_port, server_dir) in SERVERS.items():
        scenario(script, rcon_port)
        stats = load_json(server_dir, "stats")
        advancements = load_json(server_dir, "advancements")
        print(label + " stats       : " + json.dumps(normalise_stats(stats or {}), sort_keys=True))
        print(label + " advancements: %d entries" % len(normalise_advancements(advancements or {})))
        result[label] = (normalise_stats(stats or {}), normalise_advancements(advancements or {}))
    ok = True
    for index, name in enumerate(("stats", "advancements")):
        ref, sap = result["ref"][index], result["sapling"][index]
        if ref != sap:
            ok = False
            print("DIFF " + name)
            if isinstance(ref, dict) and isinstance(sap, dict):
                for key in sorted(set(ref) | set(sap)):
                    if ref.get(key) != sap.get(key):
                        print("  " + str(key))
                        print("    ref: " + json.dumps(ref.get(key), sort_keys=True)[:400])
                        print("    sap: " + json.dumps(sap.get(key), sort_keys=True)[:400])
    print("player data file parity: " + ("MATCH" if ok else "DIFFERED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
