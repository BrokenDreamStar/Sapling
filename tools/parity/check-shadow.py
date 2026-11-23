#!/usr/bin/env python3
"""Shadow parity check: /player <real-player> shadow replaces the connection.

A minimal protocol client (tools/parity/mc_client.py) logs in as a real player.
The console then runs /player <name> shadow, which must disconnect that client
with "multiplayer.disconnect.duplicate_login" and leave a controllable fake
player with the same name/position/health behind.
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
PRINTABLE_RE = re.compile(rb"[ -~]{4,}")


def run(script, *args):
    subprocess.run(
        [os.path.join(ROOT, "tools", "parity", script), *args],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def decode_component(hex_text):
    """Extract human readable strings from a network-NBT component blob."""
    try:
        data = bytes.fromhex(hex_text)
    except ValueError:
        return hex_text
    return [match.decode("ascii") for match in PRINTABLE_RE.findall(data)]


def parse_pos(output):
    match = re.search(r"\[(-?[0-9.]+)d, (-?[0-9.]+)d, (-?[0-9.]+)d\]", output)
    if not match:
        return None
    return tuple(float(value) for value in match.groups())


def wait_until(rcon, command, expect, timeout=10.0):
    deadline = time.monotonic() + timeout
    output = ""
    while time.monotonic() < deadline:
        output = rcon.command(command)
        if expect in output:
            return output
        time.sleep(0.1)
    return output


def probe(script, rcon_port, game_port, name):
    run(script, "--reset-world", "--wait-ready")
    result = {}
    client = connect("127.0.0.1", game_port, name)
    try:
        with parity.Rcon("127.0.0.1", rcon_port, "parity") as rcon:
            rcon.command("/gamerule spawn_mobs false")
            rcon.command("/gamerule advance_time false")
            rcon.command("/time set midnight")
            rcon.command("/kill @e[type=minecraft:zombie]")
            rcon.command("/kill @e[type=minecraft:slime]")
            client.wait_play(20)
            wait_until(rcon, "/list", name)
            result["list_before"] = parity.normalize(rcon.command("/list"))
            result["shadow_reply"] = parity.normalize(rcon.command("/player " + name + " shadow"))
            client.wait_closed(8.0)
            result["client_disconnect"] = sorted(
                re.sub(r"^[^A-Za-z]+", "", item)
                for item in decode_component((client.disconnect_info or "").split(": ", 1)[-1])
                if "multiplayer." in item or item.startswith("translate") or item.startswith("&multiplayer.")
            )
            wait_until(rcon, "/execute if entity " + name, "Test passed")
            result["entity"] = parity.normalize(rcon.command("/execute if entity " + name))
            result["list_after"] = parity.normalize(rcon.command("/list"))
            result["health"] = parity.normalize(rcon.command("/data get entity " + name + " Health"))
            bot_pos = parse_pos(rcon.command("/data get entity " + name + " Pos"))
            result["move_reply"] = parity.normalize(rcon.command("/player " + name + " move forward"))
            time.sleep(1.0)
            rcon.command("/player " + name + " stop")
            moved_pos = parse_pos(rcon.command("/data get entity " + name + " Pos"))
            if bot_pos and moved_pos:
                result["move_delta"] = tuple(round(moved_pos[i] - bot_pos[i], 1) for i in range(3))
            else:
                result["move_delta"] = None
            result["kill_reply"] = parity.normalize(rcon.command("/player " + name + " kill"))
            time.sleep(0.5)
            result["list_final"] = parity.normalize(rcon.command("/list"))
    finally:
        client.close()
        run(script, "--stop")
    return result


def main():
    # same name on both servers (probes run sequentially) so all captured
    # strings are directly comparable
    ref = probe(*SERVERS["ref"], "ShadowTest")
    sap = probe(*SERVERS["sapling"], "ShadowTest")
    print("ref    :", ref)
    print("sapling:", sap)
    ok = True
    for key in sorted(set(ref) | set(sap)):
        if key == "move_delta":
            a, b = ref.get(key), sap.get(key)
            if a is None or b is None or any(abs(a[i] - b[i]) > 0.5 for i in range(3)):
                ok = False
                print("DIFF " + key + ":", a, "!=", b)
            continue
        if ref.get(key) != sap.get(key):
            ok = False
            print("DIFF " + key + ":")
            print("  ref:", ref.get(key))
            print("  sap:", sap.get(key))
    run("run-carpet.sh", "--stop")
    print("shadow parity: " + ("MATCH" if ok else "DIFFERED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
