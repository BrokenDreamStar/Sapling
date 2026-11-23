#!/usr/bin/env python3
"""Compare the allowSpawningOfflinePlayers=false config branch.

The rule only has an effect in online mode (in offline mode both servers resolve
any name to an offline UUID regardless of the setting). Carpet exposes it as a
runtime rule; Sapling reads leaves.yml, so the Sapling server is restarted with
the edited config. Both servers run in online mode with the system proxy as JVM
options for the profile resolver.
"""

import os
import random
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "parity"))
from parity import Rcon  # noqa: E402

CARPET_RCON = 25575
SAPLING_RCON = 25576
LEAVES_YML = os.path.join(ROOT, ".carpet-ref", "parity", "sapling-server", "leaves.yml")
CARPET_PROPS = os.path.join(ROOT, ".carpet-ref", "parity", "carpet-server", "server.properties")
PROXY_OPTS = (
    "-Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=7897 "
    "-Dhttp.proxyHost=127.0.0.1 -Dhttp.proxyPort=7897"
)


def run(script, *args, online=None):
    env = os.environ.copy()
    if online is not None:
        env["PARITY_ONLINE_MODE"] = "true" if online else "false"
        env["PARITY_JAVA_OPTS"] = PROXY_OPTS
    subprocess.run(
        [os.path.join(ROOT, "tools", "parity", script), *args],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def set_carpet_online_mode(value):
    with open(CARPET_PROPS, "r", encoding="utf-8") as handle:
        text = handle.read()
    with open(CARPET_PROPS, "w", encoding="utf-8") as handle:
        handle.write(re.sub(r"^online-mode=.*$", "online-mode=" + value, text, count=1, flags=re.MULTILINE))


def set_sapling_setting(value):
    with open(LEAVES_YML, "r", encoding="utf-8") as handle:
        text = handle.read()
    if not re.search(r"allow-spawning-offline-players: (true|false)", text):
        raise SystemExit("could not find allow-spawning-offline-players in leaves.yml")
    updated = re.sub(
        r"(allow-spawning-offline-players: )(true|false)", lambda match: match.group(1) + value, text, count=1
    )
    with open(LEAVES_YML, "w", encoding="utf-8") as handle:
        handle.write(updated)


def spawn_probe(rcon_port, name):
    with Rcon("127.0.0.1", rcon_port, "parity") as rcon:
        rcon.command("/player " + name + " kill")
        time.sleep(0.3)
        reply = rcon.command("/player " + name + " spawn")
        time.sleep(2.5)
        entity = rcon.command("/execute if entity " + name)
        listed = rcon.command("/list")
        rcon.command("/player " + name + " kill")
    return reply, entity, listed


def restore_defaults():
    run("run-carpet.sh", "--stop")
    run("run-sapling.sh", "--stop")
    set_sapling_setting("true")
    set_carpet_online_mode("false")


def main():
    random.seed()
    suffix = str(random.randint(100000, 999999))
    name_off = "OffTestA" + suffix
    name_on = "OffTestB" + suffix

    run("run-carpet.sh", "--stop")
    run("run-sapling.sh", "--stop")

    # --- allowSpawningOfflinePlayers = false (online mode) ---------------
    set_sapling_setting("false")
    set_carpet_online_mode("true")
    run("run-carpet.sh", "--reset-world", "--wait-ready", online=True)
    run("run-sapling.sh", "--reset-world", "--wait-ready", online=True)
    with Rcon("127.0.0.1", CARPET_RCON, "parity") as rcon:
        print("carpet set current false:", repr(rcon.command("/carpet allowSpawningOfflinePlayers false")))
    ref_off = spawn_probe(CARPET_RCON, name_off)
    sap_off = spawn_probe(SAPLING_RCON, name_off)
    print("ref     offline-disabled:", ref_off)
    print("sapling offline-disabled:", sap_off)

    # --- allowSpawningOfflinePlayers = true (online mode) ----------------
    with Rcon("127.0.0.1", CARPET_RCON, "parity") as rcon:
        print("carpet set current true:", repr(rcon.command("/carpet allowSpawningOfflinePlayers true")))
        print("carpet setDefault true:", repr(rcon.command("/carpet setDefault allowSpawningOfflinePlayers true")))
    set_sapling_setting("true")
    run("run-sapling.sh", "--stop")
    run("run-sapling.sh", "--wait-ready", online=True)
    ref_on = spawn_probe(CARPET_RCON, name_on)
    sap_on = spawn_probe(SAPLING_RCON, name_on)
    print("ref     offline-enabled :", ref_on)
    print("sapling offline-enabled :", sap_on)

    ok = ref_off == sap_off and ref_on == sap_on
    if not ok:
        print("DIFF off:", ref_off, "!=", sap_off)
        print("DIFF on :", ref_on, "!=", sap_on)
    print("config branch parity: " + ("MATCH" if ok else "DIFFERED"))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        restore_defaults()
    sys.exit(code)
