#!/usr/bin/env python3
"""Permission parity for the /player command as seen by a real (chat) client.

A minimal protocol client logs in with permission level 0 and sends the
unsigned command packet. With commandPlayer=ops the command is denied until the
player is opped; with commandPlayer=false it stays denied even for an op. Both
servers must behave the same (no fake player is created; the op case creates
one on both).
"""

import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "parity"))
import parity  # noqa: E402
from mc_client import connect  # noqa: E402

SERVERS = {
    "ref": ("run-carpet.sh", 25575, 25565),
    "sapling": ("run-sapling.sh", 25576, 25566),
}
LEAVES_YML = os.path.join(ROOT, ".carpet-ref", "parity", "sapling-server", "leaves.yml")
CLIENT_NAME = "PermClient"
FAKE_NAME = "PermFake"


def run(script, *args):
    subprocess.run(
        [os.path.join(ROOT, "tools", "parity", script), *args],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def set_sapling_command_player(value):
    with open(LEAVES_YML, "r", encoding="utf-8") as handle:
        text = handle.read()
    if not re.search(r"command-player: [^\n]+", text):
        raise SystemExit("command-player not found in leaves.yml")
    text = re.sub(r"(command-player: )[^\n]+", lambda match: match.group(1) + value, text, count=1)
    with open(LEAVES_YML, "w", encoding="utf-8") as handle:
        handle.write(text)


def wait_until(rcon, command, expect, timeout=10.0):
    deadline = time.monotonic() + timeout
    output = ""
    while time.monotonic() < deadline:
        output = rcon.command(command)
        if expect in output:
            return output
        time.sleep(0.1)
    return output


def probe(script, rcon_port, game_port, command_player):
    if script == "run-sapling.sh":
        set_sapling_command_player(command_player)
        run(script, "--reset-world", "--wait-ready")
        configure = None
    else:
        run(script, "--reset-world", "--wait-ready")
        configure = command_player

    result = {}
    client = connect("127.0.0.1", game_port, CLIENT_NAME)
    try:
        with parity.Rcon("127.0.0.1", rcon_port, "parity") as rcon:
            if configure is not None:
                rcon.command("/carpet commandPlayer " + configure)
            rcon.command("/gamerule spawn_mobs false")
            rcon.command("/gamerule advance_time false")
            rcon.command("/time set midnight")
            rcon.command("/kill @e[type=!minecraft:player]")
            client.wait_play(20)
            wait_until(rcon, "/list", CLIENT_NAME)
            result["list_initial"] = parity.normalize(rcon.command("/list"))

            client.send_chat_command("player " + FAKE_NAME + " spawn")
            time.sleep(2.5)
            result["entity_player"] = parity.normalize(rcon.command("/execute if entity " + FAKE_NAME))
            result["list_player"] = parity.normalize(rcon.command("/list"))

            rcon.command("/op " + CLIENT_NAME)
            time.sleep(1.0)
            client.send_chat_command("player " + FAKE_NAME + " spawn")
            result["entity_op"] = parity.normalize(
                wait_until(rcon, "/execute if entity " + FAKE_NAME, "Test passed")
            )
            result["list_op"] = parity.normalize(rcon.command("/list"))
            rcon.command("/player " + FAKE_NAME + " kill")
            rcon.command("/deop " + CLIENT_NAME)
            time.sleep(0.5)
    finally:
        client.close()
        run(script, "--stop")
    return result


def main():
    ok = True
    for command_player in ("ops", "false", "0", "1"):
        ref = probe(*SERVERS["ref"], command_player)
        sap = probe(*SERVERS["sapling"], command_player)
        print("## commandPlayer=" + command_player)
        print("ref    :", ref)
        print("sapling:", sap)
        for key in sorted(set(ref) | set(sap)):
            if ref.get(key) != sap.get(key):
                ok = False
                print("DIFF " + key + ":")
                print("  ref:", ref.get(key))
                print("  sap:", sap.get(key))

    # restore defaults
    run("run-sapling.sh", "--stop")
    set_sapling_command_player("ops")
    run("run-sapling.sh", "--reset-world", "--wait-ready")
    run("run-sapling.sh", "--stop")
    run("run-carpet.sh", "--stop")

    print("client permission parity: " + ("MATCH" if ok else "DIFFERED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
