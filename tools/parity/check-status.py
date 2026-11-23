#!/usr/bin/env python3
"""Compare server-list-ping (SLP) player sample semantics of both servers.

Carpet's allowListingFakePlayers=false (default) hides fake players from the
server list sample while they still count as online; Sapling ports the same
rule through FakePlayer.allowsListing(). This script spawns a fake player over
RCON, asks each server for its status JSON and compares the players section.
"""

import json
import socket
import struct
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from parity import Rcon  # noqa: E402

REF = ("127.0.0.1", 25575, 25565)      # RCON port, game port
SAPLING = ("127.0.0.1", 25576, 25566)
PASSWORD = "parity"


from check_status_helpers import players_view, status  # noqa: E402


def run_for(label, rcon_port, game_port):
    with Rcon("127.0.0.1", rcon_port, PASSWORD) as rcon:
        rcon.command("/player StatusBot kill")
        rcon.command("/player StatusBot spawn")
        time.sleep(3)
        online = players_view(status("127.0.0.1", game_port))
        rcon.command("/player StatusBot kill")
    # Both servers cache the ping response for up to 5 s; wait for a rebuild.
    time.sleep(6.0)
    offline = players_view(status("127.0.0.1", game_port))
    print(label + " with fake online : " + json.dumps(online, sort_keys=True))
    print(label + " fake killed      : " + json.dumps(offline, sort_keys=True))
    return online, offline


def main():
    ref = run_for("ref     ", REF[1], REF[2])
    sap = run_for("sapling ", SAPLING[1], SAPLING[2])
    ok = True
    for name, a, b in (("with-fake", ref[0], sap[0]), ("no-fake", ref[1], sap[1])):
        if a != b:
            ok = False
            print("DIFF " + name + ":")
            print("  ref     " + json.dumps(a, sort_keys=True))
            print("  sapling " + json.dumps(b, sort_keys=True))
    print("status parity: " + ("MATCH" if ok else "DIFFERED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
