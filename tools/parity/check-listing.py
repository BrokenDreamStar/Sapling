#!/usr/bin/env python3
"""Compare the allowListingFakePlayers=true branch on the server-list ping.

Carpet defaults this rule to false (fake players appear as an anonymous sample
entry, checked by check-status.py). With the rule enabled the fake player's name
must appear in the SLP sample on both servers.
"""

import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "parity"))
import parity  # noqa: E402
from check_status_helpers import players_view, status  # noqa: E402  (installed below)

CARPET_RCON = 25575
SAPLING_RCON = 25576
GAME_PORTS = {CARPET_RCON: 25565, SAPLING_RCON: 25566}
LEAVES_YML = os.path.join(ROOT, ".carpet-ref", "parity", "sapling-server", "leaves.yml")


def run(script, *args):
    subprocess.run(
        [os.path.join(ROOT, "tools", "parity", script), *args],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def set_sapling_listing(value):
    with open(LEAVES_YML, "r", encoding="utf-8") as handle:
        text = handle.read()
    if not re.search(r"allow-listing-fake-players: [^\n]+", text):
        raise SystemExit("allow-listing-fake-players not found in leaves.yml")
    text = re.sub(
        r"(allow-listing-fake-players: )[^\n]+", lambda match: match.group(1) + value, text, count=1
    )
    with open(LEAVES_YML, "w", encoding="utf-8") as handle:
        handle.write(text)


def probe(rcon_port):
    game_port = GAME_PORTS[rcon_port]
    with parity.Rcon("127.0.0.1", rcon_port, "parity") as rcon:
        rcon.command("/player ListBot kill")
        rcon.command("/player ListBot spawn")
        time.sleep(3)
        with_fake = players_view(status("127.0.0.1", game_port))
        rcon.command("/player ListBot kill")
    time.sleep(6)
    without_fake = players_view(status("127.0.0.1", game_port))
    return with_fake, without_fake


def main():
    run("run-carpet.sh", "--stop")
    run("run-sapling.sh", "--stop")
    set_sapling_listing("true")
    run("run-carpet.sh", "--reset-world", "--wait-ready")
    run("run-sapling.sh", "--reset-world", "--wait-ready")
    with parity.Rcon("127.0.0.1", CARPET_RCON, "parity") as rcon:
        print("carpet allowListingFakePlayers true:", repr(rcon.command("/carpet allowListingFakePlayers true")))
    ref = probe(CARPET_RCON)
    sap = probe(SAPLING_RCON)
    print("ref     with fake:", json.dumps(ref[0], sort_keys=True))
    print("sapling with fake:", json.dumps(sap[0], sort_keys=True))
    print("ref     no fake  :", json.dumps(ref[1], sort_keys=True))
    print("sapling no fake  :", json.dumps(sap[1], sort_keys=True))

    # restore
    with parity.Rcon("127.0.0.1", CARPET_RCON, "parity") as rcon:
        print("carpet allowListingFakePlayers false:", repr(rcon.command("/carpet allowListingFakePlayers false")))
    run("run-carpet.sh", "--stop")
    run("run-sapling.sh", "--stop")
    set_sapling_listing("false")
    run("run-sapling.sh", "--reset-world", "--wait-ready")
    run("run-sapling.sh", "--stop")

    ok = ref == sap
    print("listing config parity: " + ("MATCH" if ok else "DIFFERED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
